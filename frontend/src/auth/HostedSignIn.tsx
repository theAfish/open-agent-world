import { useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { Eye, EyeOff, KeyRound, LockKeyhole, LogOut, UserPlus } from 'lucide-react';
import { signOutHosted, type HostedSession } from './hostedSession';
import './hosted.css';

function PasswordInput({ label, headerAction, value, onChange, disabled, placeholder, autoComplete, minLength }: {
  label: string;
  headerAction?: ReactNode;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  autoComplete?: string;
  minLength?: number;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const zh = navigator.language.toLowerCase().startsWith('zh');
  return (
    <div className="hosted-field">
      <div className="hosted-field-head">
        <label htmlFor={id}>{label}</label>
        {headerAction}
      </div>
      <div className="hosted-pwd-wrap">
        <input
          id={id}
          name="password"
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          required
          minLength={minLength}
          maxLength={1024}
          placeholder={placeholder}
          value={value}
          onChange={event => onChange(event.target.value)}
          disabled={disabled}
        />
        <button
          type="button"
          className="hosted-eye-btn"
          aria-label={visible ? (zh ? '隐藏密码' : 'Hide password') : (zh ? '显示密码' : 'Show password')}
          onClick={() => setVisible(v => !v)}
          disabled={disabled}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </div>
  );
}

function useCooldown() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (seconds <= 0) return;
    const timer = setTimeout(() => setSeconds(value => Math.max(0, value - 1)), 1000);
    return () => clearTimeout(timer);
  }, [seconds]);
  return [seconds, setSeconds] as const;
}

function isValidUserPassword(value: string) {
  return value.length >= 8 && /[A-Z]/.test(value) && /[a-z]/.test(value) && /\d/.test(value);
}

async function postJson(path: string, body: Record<string, unknown>) {
  return fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
}

/** No persisted application stores are imported until identity is established. */
export function HostedSignIn({ session }: { session: HostedSession }) {
  const chinese = navigator.language.toLowerCase().startsWith('zh');
  const copy = (en: string, zh: string) => chinese ? zh : en;
  const codeEnabled = Boolean(session.registration_enabled || session.login_methods.includes('code'));
  const baseId = useId();

  const [card, setCard] = useState<'login' | 'register' | 'reset'>('login');
  const [loginMode, setLoginMode] = useState<'pwd' | 'code'>('pwd');
  const [busy, setBusy] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [error, setError] = useState('');

  // Login states
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginTarget, setLoginTarget] = useState('');
  const [loginCode, setLoginCode] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [loginCooldown, setLoginCooldown] = useCooldown();

  // Registration 2-step states
  const [regStep, setRegStep] = useState<1 | 2>(1);
  const [regUsername, setRegUsername] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regEmailCode, setRegEmailCode] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regPasswordConfirm, setRegPasswordConfirm] = useState('');
  const [registerTicket, setRegisterTicket] = useState('');
  const [verifiedEmail, setVerifiedEmail] = useState('');
  const [regPhone, setRegPhone] = useState('');
  const [regPhoneCode, setRegPhoneCode] = useState('');
  const [regEmailCooldown, setRegEmailCooldown] = useCooldown();
  const [regPhoneCooldown, setRegPhoneCooldown] = useCooldown();

  // Password reset states
  const [resetTarget, setResetTarget] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [resetPassword, setResetPassword] = useState('');
  const [resetPasswordConfirm, setResetPasswordConfirm] = useState('');
  const [resetCooldown, setResetCooldown] = useCooldown();

  // Poll when a newly registered user's workspace is being provisioned
  useEffect(() => {
    if (!session.authenticated || session.instance || !session.provisioning) return;
    const timer = setInterval(async () => {
      try {
        const res = await fetch('/auth/me', { cache: 'no-store', credentials: 'same-origin' });
        if (!res.ok) return;
        const next = await res.json() as HostedSession;
        if (next.instance) window.location.replace('/');
      } catch { /* Retry on next interval. */ }
    }, 2000);
    return () => clearInterval(timer);
  }, [session.authenticated, session.instance, session.provisioning]);

  const switchCard = (next: 'login' | 'register' | 'reset') => {
    setError('');
    setCard(next);
  };

  const requestVerificationCode = async (
    target: string,
    purpose: 'login' | 'register_email' | 'register_phone' | 'reset_password',
    startCooldown: (s: number) => void,
  ) => {
    const trimmed = target.trim();
    if (!trimmed) {
      setError(purpose === 'register_email'
        ? copy('Enter your email address first.', '请先输入邮箱地址。')
        : purpose === 'register_phone'
          ? copy('Enter your phone number first.', '请先输入手机号。')
          : copy('Enter your email or phone number first.', '请先输入邮箱或手机号。'));
      return;
    }
    if (sendingCode || busy) return;
    setSendingCode(true);
    setError('');
    try {
      const response = await postJson('/auth/code/send', { target: trimmed, purpose });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const errCode = payload?.error?.code;
        if (response.status === 409 && errCode === 'email_registered') {
          setError(copy('This email is already registered. Sign in instead.', '该邮箱已注册，请直接登录。'));
        } else if (response.status === 429) {
          setError(copy('Please wait before requesting another code.', '请求过于频繁，请稍后再试。'));
        } else if (response.status === 422) {
          setError(purpose === 'register_phone'
            ? copy('Enter a valid phone number.', '请输入有效的手机号。')
            : copy('Enter a valid email or phone number.', '请输入有效的邮箱或手机号。'));
        } else {
          setError(copy('Could not send verification code. Try again.', '验证码发送失败，请稍后重试。'));
        }
        return;
      }
      const data = await response.json().catch(() => ({ cooldown_seconds: 60 }));
      startCooldown(Number(data?.cooldown_seconds) || 60);
    } catch {
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setSendingCode(false);
    }
  };

  const submitPasswordLogin = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const payload: Record<string, unknown> = { username: username.trim(), password };
      if (codeEnabled) payload.remember_me = rememberMe;
      const response = await postJson('/auth/login', payload);
      setPassword('');
      if (!response.ok) {
        setError(response.status === 429
          ? copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。')
          : copy('Unable to sign in. Check your username and password.', '登录失败，请检查账号和密码。'));
        return;
      }
      window.location.replace('/');
    } catch {
      setPassword('');
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setBusy(false);
    }
  };

  const submitCodeLogin = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const response = await postJson('/auth/login/code', {
        target: loginTarget.trim(),
        code: loginCode.trim(),
        remember_me: rememberMe,
      });
      if (!response.ok) {
        setError(response.status === 429
          ? copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。')
          : copy('Invalid verification code or unregistered account.', '验证码错误或该账号尚未注册。'));
        return;
      }
      window.location.replace('/');
    } catch {
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setBusy(false);
    }
  };

  const submitRegisterStep1 = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!/^[a-zA-Z][a-zA-Z0-9_.-]{1,31}$/.test(regUsername.trim())) {
      setError(copy(
        'Username must start with a letter and contain 2–32 letters, digits, ., _, or -.',
        '用户名需以字母开头，由 2–32 位字母、数字或 . _ - 组成。',
      ));
      return;
    }
    if (!isValidUserPassword(regPassword)) {
      setError(copy(
        'Password must be at least 8 characters and include uppercase, lowercase, and digits.',
        '密码至少 8 位，且需包含大写字母、小写字母和数字。',
      ));
      return;
    }
    if (regPassword !== regPasswordConfirm) {
      setError(copy('Passwords do not match.', '两次输入的密码不一致，请重新核对。'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const response = await postJson('/auth/register/email', {
        username: regUsername.trim(),
        email: regEmail.trim(),
        code: regEmailCode.trim(),
        password: regPassword,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        if (response.status === 409) {
          setError(payload?.error?.code === 'username_registered'
            ? copy('This username is already taken.', '该用户名已被占用，请更换一个。')
            : copy('This email is already registered. Sign in instead.', '该邮箱已注册，请直接登录。'));
        } else if (response.status === 429) {
          setError(copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。'));
        } else if (response.status === 422) {
          setError(payload?.error?.message || copy('Check your username, email and password.', '请检查用户名、邮箱格式或密码长度（至少 12 位）。'));
        } else {
          setError(copy('Invalid or expired email verification code.', '邮箱验证码无效或已过期。'));
        }
        return;
      }
      const data = await response.json();
      setRegisterTicket(data.register_ticket);
      setVerifiedEmail(data.email || regEmail.trim());
      setRegPassword('');
      setRegPasswordConfirm('');
      setRegStep(2);
    } catch {
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setBusy(false);
    }
  };

  const submitRegisterStep2 = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const response = await postJson('/auth/register/complete', {
        register_ticket: registerTicket,
        phone: regPhone.trim(),
        code: regPhoneCode.trim(),
        remember_me: true,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        if (response.status === 409) {
          setError(copy('Email and phone belong to different existing accounts.', '该手机号与邮箱分属不同已有账号，请直接登录原账号换绑。'));
        } else if (response.status === 429) {
          setError(copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。'));
        } else if (response.status === 422) {
          setError(payload?.error?.message?.includes('expired')
            ? copy('Email verification expired. Please verify your email again.', '邮箱验证已超时，请返回上一步重新验证。')
            : copy('Enter a valid phone number.', '请输入有效的手机号。'));
        } else {
          setError(copy('Invalid or expired SMS verification code.', '短信验证码无效或已过期。'));
        }
        return;
      }
      window.location.replace('/');
    } catch {
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setBusy(false);
    }
  };

  const submitPasswordReset = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!isValidUserPassword(resetPassword)) {
      setError(copy(
        'Password must be at least 8 characters and include uppercase, lowercase, and digits.',
        '新密码至少 8 位，且需包含大写字母、小写字母和数字。',
      ));
      return;
    }
    if (resetPassword !== resetPasswordConfirm) {
      setError(copy('Passwords do not match.', '两次输入的新密码不一致。'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const response = await postJson('/auth/password/reset', {
        target: resetTarget.trim(),
        code: resetCode.trim(),
        password: resetPassword,
        remember_me: true,
      });
      if (!response.ok) {
        setError(response.status === 429
          ? copy('Too many attempts. Wait a moment and try again.', '尝试次数过多，请稍后再试。')
          : copy('Invalid verification code or unregistered account.', '验证码无效或该账号不存在。'));
        return;
      }
      window.location.replace('/');
    } catch {
      setError(copy('Cannot connect to the server. Please try again.', '无法连接服务器，请重试。'));
    } finally {
      setBusy(false);
    }
  };

  if (session.authenticated) {
    return (
      <main className="hosted-login">
        <section className="hosted-login-card" aria-labelledby="hosted-title">
          <div className="hosted-brand-head">
            <div className="hosted-brand-icon" aria-hidden="true"><LockKeyhole size={20} /></div>
            <h1 id="hosted-title">Open Agent World</h1>
          </div>
          <p>
            {session.provisioning
              ? copy('Preparing your isolated workspace… This page will open automatically.', '正在为你准备独立工作区，就绪后将自动进入…')
              : copy('Your account has no workspace assigned. Contact your administrator.', '当前账号尚未分配工作区，请联系管理员。')}
          </p>
          <button type="button" className="secondary-button" onClick={() => { void signOutHosted(); }}>
            <LogOut size={16} />{copy('Sign out', '退出登录')}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="hosted-login">
      {card === 'login' && (
        <section className="hosted-login-card" aria-labelledby="hosted-title">
          <div className="hosted-brand-head">
            <div className="hosted-brand-icon" aria-hidden="true"><LockKeyhole size={20} /></div>
            <h1 id="hosted-title">{codeEnabled ? copy('Sign in to workspace', '登录工作区') : 'Open Agent World'}</h1>
          </div>
          {!codeEnabled && <p>{copy('Sign in to your workspace', '登录你的工作区')}</p>}

          {codeEnabled && session.login_methods.includes('password') && (
            <div className="hosted-mode-switch" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={loginMode === 'pwd'}
                className={loginMode === 'pwd' ? 'is-active' : ''}
                onClick={() => { setError(''); setLoginMode('pwd'); }}
              >
                {copy('Password', '密码登录')}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={loginMode === 'code'}
                className={loginMode === 'code' ? 'is-active' : ''}
                onClick={() => { setError(''); setLoginMode('code'); }}
              >
                {copy('Verification code', '验证码登录')}
              </button>
            </div>
          )}

          {session.login_methods.includes('password') && (!codeEnabled || loginMode === 'pwd') && (
            <form onSubmit={event => { void submitPasswordLogin(event); }}>
              <label className="hosted-field">
                <span>{copy('Username', '账号')}</span>
                <input
                  name="username"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                  maxLength={254}
                  placeholder={codeEnabled ? copy('Username, email or phone', '用户名 / 邮箱 / 手机号') : undefined}
                  value={username}
                  onChange={event => setUsername(event.target.value)}
                  disabled={busy}
                  autoFocus
                />
              </label>

              <PasswordInput
                label={copy('Password', '密码')}
                headerAction={codeEnabled ? (
                  <button type="button" className="hosted-text-link" style={{ fontSize: '0.75rem' }} onClick={() => switchCard('reset')}>
                    {copy('Forgot password?', '忘记密码？')}
                  </button>
                ) : undefined}
                autoComplete="current-password"
                value={password}
                onChange={setPassword}
                disabled={busy}
              />

              {codeEnabled && (
                <div className="hosted-check-row">
                  <label className="hosted-check-label">
                    <input type="checkbox" checked={rememberMe} onChange={e => setRememberMe(e.target.checked)} disabled={busy} />
                    <span>{copy('Keep me signed in for 30 days', '30 天内保持登录')}</span>
                  </label>
                </div>
              )}

              {error && <p role="alert" className="hosted-login-error">{error}</p>}
              <button className="primary-button" disabled={busy}>
                {busy ? copy('Signing in…', '正在登录…') : copy('Sign in', '登录')}
              </button>
            </form>
          )}

          {codeEnabled && loginMode === 'code' && (
            <form onSubmit={event => { void submitCodeLogin(event); }}>
              <label className="hosted-field">
                <span>{copy('Email or phone', '邮箱或手机号')}</span>
                <input
                  type="text"
                  required
                  maxLength={254}
                  placeholder={copy('Registered email or phone number', '已注册的邮箱或 11 位手机号')}
                  value={loginTarget}
                  onChange={event => setLoginTarget(event.target.value)}
                  disabled={busy}
                  autoFocus
                />
              </label>
              <div className="hosted-field">
                <label htmlFor={`${baseId}-login-code`}>{copy('Verification code', '验证码')}</label>
                <div className="hosted-code-row">
                  <input
                    id={`${baseId}-login-code`}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    required
                    placeholder={copy('6-digit code', '6 位验证码')}
                    value={loginCode}
                    onChange={event => setLoginCode(event.target.value)}
                    disabled={busy}
                  />
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy || sendingCode || loginCooldown > 0}
                    onClick={() => { void requestVerificationCode(loginTarget, 'login', setLoginCooldown); }}
                  >
                    {loginCooldown > 0 ? `${loginCooldown}s` : copy('Send code', '获取验证码')}
                  </button>
                </div>
              </div>
              <div className="hosted-check-row">
                <label className="hosted-check-label">
                  <input type="checkbox" checked={rememberMe} onChange={e => setRememberMe(e.target.checked)} disabled={busy} />
                  <span>{copy('Keep me signed in for 30 days', '30 天内保持登录')}</span>
                </label>
              </div>
              {error && <p role="alert" className="hosted-login-error">{error}</p>}
              <button className="primary-button" disabled={busy}>
                {busy ? copy('Signing in…', '正在登录…') : copy('Sign in', '登录')}
              </button>
            </form>
          )}

          {session.login_methods.includes('oidc') && (
            <a className="secondary-button hosted-oidc-link" href="/auth/oidc/login">
              {copy('Sign in with your organization', '使用组织账号登录')}
            </a>
          )}
          {!session.login_methods.length && (
            <p role="alert">{copy('Sign in is not configured. Contact your administrator.', '登录方式尚未配置，请联系管理员。')}</p>
          )}

          {codeEnabled ? (
            <div className="hosted-card-footer">
              <span>{copy("Don't have an account?", '还没有账号？')}</span>
              <button type="button" className="hosted-text-link" onClick={() => { setRegStep(1); switchCard('register'); }}>
                {copy('Create account', '注册账号')}
              </button>
            </div>
          ) : (
            <small>{copy('Accounts are provided by your administrator.', '账号由管理员开通。')}</small>
          )}
        </section>
      )}

      {card === 'register' && (
        <section className="hosted-login-card" aria-labelledby="hosted-reg-title">
          <div className="hosted-brand-head">
            <div className="hosted-brand-icon" aria-hidden="true"><UserPlus size={20} /></div>
            <h1 id="hosted-reg-title">{copy('Create account', '创建账号')}</h1>
          </div>

          <div className="hosted-stepper">
            <div className={`hosted-step-item ${regStep === 1 ? 'is-active' : 'is-done'}`}>
              <span className="hosted-step-num">{regStep === 1 ? '1' : '✓'}</span>
              <span>{copy('Verify email', '验证邮箱')}</span>
            </div>
            <div className="hosted-step-line" />
            <div className={`hosted-step-item ${regStep === 2 ? 'is-active' : ''}`}>
              <span className="hosted-step-num">2</span>
              <span>{copy('Bind phone', '绑定手机号')}</span>
            </div>
          </div>

          {regStep === 1 ? (
            <form onSubmit={event => { void submitRegisterStep1(event); }}>
              <label className="hosted-field">
                <span>{copy('Username', '用户名')}</span>
                <input
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
                  spellCheck={false}
                  required
                  minLength={2}
                  maxLength={32}
                  placeholder={copy('Unique username (e.g. zyc_dev)', '唯一用户名（字母开头，2–32 位）')}
                  value={regUsername}
                  onChange={event => { setError(''); setRegUsername(event.target.value); }}
                  disabled={busy}
                  autoFocus
                />
              </label>

              <label className="hosted-field">
                <span>{copy('Email', '邮箱')}</span>
                <input
                  type="email"
                  autoComplete="email"
                  required
                  maxLength={254}
                  placeholder="name@example.com"
                  value={regEmail}
                  onChange={event => setRegEmail(event.target.value)}
                  disabled={busy}
                />
              </label>

              <div className="hosted-field">
                <label htmlFor={`${baseId}-reg-email-code`}>{copy('Email code', '邮箱验证码')}</label>
                <div className="hosted-code-row">
                  <input
                    id={`${baseId}-reg-email-code`}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    required
                    placeholder={copy('6-digit code', '6 位验证码')}
                    value={regEmailCode}
                    onChange={event => setRegEmailCode(event.target.value)}
                    disabled={busy}
                  />
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy || sendingCode || regEmailCooldown > 0}
                    onClick={() => { void requestVerificationCode(regEmail, 'register_email', setRegEmailCooldown); }}
                  >
                    {regEmailCooldown > 0 ? `${regEmailCooldown}s` : copy('Send code', '获取验证码')}
                  </button>
                </div>
              </div>

              <PasswordInput
                label={copy('Set password', '设置密码')}
                autoComplete="new-password"
                minLength={8}
                placeholder={copy('8+ chars with upper, lower & digit', '至少 8 位，含大小写字母和数字')}
                value={regPassword}
                onChange={v => { setError(''); setRegPassword(v); }}
                disabled={busy}
              />

              <PasswordInput
                label={copy('Confirm password', '确认密码')}
                autoComplete="new-password"
                minLength={8}
                placeholder={copy('Re-enter password', '再次输入密码')}
                value={regPasswordConfirm}
                onChange={v => { setError(''); setRegPasswordConfirm(v); }}
                disabled={busy}
              />

              {error && <p role="alert" className="hosted-login-error">{error}</p>}
              <button className="primary-button" disabled={busy}>
                {busy ? copy('Verifying…', '正在验证…') : copy('Next', '下一步')}
              </button>
            </form>
          ) : (
            <form onSubmit={event => { void submitRegisterStep2(event); }}>
              <div className="hosted-verified-pill">
                <span>✓ {verifiedEmail} {copy('verified', '已验证')}</span>
                <button
                  type="button"
                  className="hosted-text-link"
                  style={{ fontSize: '0.75rem' }}
                  onClick={() => { setError(''); setRegStep(1); }}
                >
                  {copy('Change', '修改')}
                </button>
              </div>

              <div className="hosted-field">
                <label htmlFor={`${baseId}-reg-phone`}>{copy('Phone number', '手机号')}</label>
                <div className="hosted-phone-group">
                  <span className="hosted-phone-prefix" aria-hidden="true">+86</span>
                  <input
                    id={`${baseId}-reg-phone`}
                    type="tel"
                    inputMode="tel"
                    maxLength={20}
                    required
                    placeholder={copy('11-digit mobile number', '输入 11 位手机号')}
                    value={regPhone}
                    onChange={event => setRegPhone(event.target.value)}
                    disabled={busy}
                    autoFocus
                  />
                </div>
              </div>

              <div className="hosted-field">
                <label htmlFor={`${baseId}-reg-phone-code`}>{copy('SMS code', '短信验证码')}</label>
                <div className="hosted-code-row">
                  <input
                    id={`${baseId}-reg-phone-code`}
                    type="text"
                    inputMode="numeric"
                    maxLength={6}
                    required
                    placeholder={copy('6-digit SMS code', '6 位短信验证码')}
                    value={regPhoneCode}
                    onChange={event => setRegPhoneCode(event.target.value)}
                    disabled={busy}
                  />
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy || sendingCode || regPhoneCooldown > 0}
                    onClick={() => { void requestVerificationCode(regPhone, 'register_phone', setRegPhoneCooldown); }}
                  >
                    {regPhoneCooldown > 0 ? `${regPhoneCooldown}s` : copy('Send code', '获取验证码')}
                  </button>
                </div>
              </div>

              {error && <p role="alert" className="hosted-login-error">{error}</p>}
              <button className="primary-button" disabled={busy}>
                {busy ? copy('Completing registration…', '正在完成注册…') : copy('Complete registration', '完成注册')}
              </button>
            </form>
          )}

          <div className="hosted-card-footer">
            <span>{copy('Already have an account?', '已有账号？')}</span>
            <button type="button" className="hosted-text-link" onClick={() => switchCard('login')}>
              {copy('Back to sign in', '返回登录')}
            </button>
          </div>
        </section>
      )}

      {card === 'reset' && (
        <section className="hosted-login-card" aria-labelledby="hosted-reset-title">
          <div className="hosted-brand-head">
            <div className="hosted-brand-icon" aria-hidden="true"><KeyRound size={20} /></div>
            <h1 id="hosted-reset-title">{copy('Reset password', '重置密码')}</h1>
          </div>

          <form onSubmit={event => { void submitPasswordReset(event); }}>
            <label className="hosted-field">
              <span>{copy('Bound email or phone', '已绑定的手机号或邮箱')}</span>
              <input
                type="text"
                required
                maxLength={254}
                placeholder={copy('Enter phone number or email', '输入手机号或邮箱')}
                value={resetTarget}
                onChange={event => setResetTarget(event.target.value)}
                disabled={busy}
                autoFocus
              />
            </label>

            <div className="hosted-field">
              <label htmlFor={`${baseId}-reset-code`}>{copy('Verification code', '验证码')}</label>
              <div className="hosted-code-row">
                <input
                  id={`${baseId}-reset-code`}
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  required
                  placeholder={copy('6-digit code', '6 位验证码')}
                  value={resetCode}
                  onChange={event => setResetCode(event.target.value)}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || sendingCode || resetCooldown > 0}
                  onClick={() => { void requestVerificationCode(resetTarget, 'reset_password', setResetCooldown); }}
                >
                  {resetCooldown > 0 ? `${resetCooldown}s` : copy('Send code', '获取验证码')}
                </button>
              </div>
            </div>

            <PasswordInput
              label={copy('New password', '新密码')}
              autoComplete="new-password"
              minLength={8}
              placeholder={copy('8+ chars with upper, lower & digit', '至少 8 位，含大小写字母和数字')}
              value={resetPassword}
              onChange={v => { setError(''); setResetPassword(v); }}
              disabled={busy}
            />

            <PasswordInput
              label={copy('Confirm new password', '确认新密码')}
              autoComplete="new-password"
              minLength={8}
              placeholder={copy('Re-enter new password', '再次输入新密码')}
              value={resetPasswordConfirm}
              onChange={v => { setError(''); setResetPasswordConfirm(v); }}
              disabled={busy}
            />

            {error && <p role="alert" className="hosted-login-error">{error}</p>}
            <button className="primary-button" disabled={busy}>
              {busy ? copy('Updating…', '正在更新…') : copy('Update password & sign in', '更新密码并登录')}
            </button>
          </form>

          <div className="hosted-card-footer">
            <button type="button" className="hosted-text-link" onClick={() => switchCard('login')}>
              {copy('Back to sign in', '返回登录')}
            </button>
          </div>
        </section>
      )}
    </main>
  );
}
