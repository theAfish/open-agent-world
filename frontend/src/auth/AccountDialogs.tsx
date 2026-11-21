import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { Modal } from '../components/Modal';
import { useLocale } from '../i18n';
import { currentHostedSession, updateHostedSessionUser } from './hostedSession';

interface AccountDetail {
  id: string;
  username: string;
  display_name: string | null;
  role: 'user' | 'admin';
  email: string | null;
  phone_masked: string | null;
  has_password: boolean;
  session_persistent: boolean;
  session_expires_at: number | null;
  active_sessions: number;
}

interface AdminUserItem {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  phone_masked: string | null;
  role: 'user' | 'admin';
  enabled: boolean;
  created_at: number;
  last_login_at: number | null;
  instance: { id: string; application_id: string; name: string; mode: 'builder' | 'runtime' } | null;
  provisioning: boolean;
}

function isValidUserPassword(value: string) {
  return value.length >= 8 && /[A-Z]/.test(value) && /[a-z]/.test(value) && /\d/.test(value);
}

function ModalPasswordField({ label, value, onChange, disabled, placeholder, minLength }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  minLength?: number;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const zh = useLocale(s => s.locale) === 'zh-CN';
  return (
    <div className="hosted-field">
      <label htmlFor={id}>{label}</label>
      <div className="hosted-pwd-wrap">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          minLength={minLength}
          maxLength={1024}
          required
          placeholder={placeholder}
          value={value}
          onChange={e => onChange(e.target.value)}
          disabled={disabled}
        />
        <button
          type="button"
          className="hosted-eye-btn"
          aria-label={visible ? (zh ? '隐藏密码' : 'Hide password') : (zh ? '显示密码' : 'Show password')}
          onClick={() => setVisible(v => !v)}
          disabled={disabled}
        >
          {visible ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      </div>
    </div>
  );
}

export function AccountSettingsModal({ onClose }: { onClose: () => void }) {
  const zh = useLocale(s => s.locale) === 'zh-CN';
  const copy = (en: string, zhText: string) => (zh ? zhText : en);
  const session = currentHostedSession();
  const canVerify = Boolean(session?.registration_enabled || session?.login_methods.includes('code'));

  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [displayName, setDisplayName] = useState(session?.user?.display_name || session?.user?.username || '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [activeEditor, setActiveEditor] = useState<null | 'email' | 'phone' | 'password'>(null);

  const [bindTarget, setBindTarget] = useState('');
  const [bindCode, setBindCode] = useState('');
  const [cooldown, setCooldown] = useState(0);

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown(v => Math.max(0, v - 1)), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const loadAccount = useCallback(async () => {
    try {
      const res = await fetch('/auth/account', { cache: 'no-store', credentials: 'same-origin' });
      if (!res.ok) return;
      const body = await res.json();
      if (body?.user) {
        setDetail(body.user);
        setDisplayName(body.user.display_name || body.user.username || '');
      }
    } catch { /* Keep session fallback. */ }
  }, []);

  useEffect(() => { void loadAccount(); }, [loadAccount]);

  const saveDisplayName = async () => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/auth/account', {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: displayName.trim() }),
      });
      if (!res.ok) {
        setError(copy('Could not update display name.', '保存显示名称失败。'));
        return;
      }
      const body = await res.json();
      setDetail(body.user);
      updateHostedSessionUser({ display_name: body.user.display_name || body.user.username });
      setNotice(copy('Display name saved.', '已保存显示名称。'));
    } finally {
      setBusy(false);
    }
  };

  const sendBindCode = async (kind: 'email' | 'phone') => {
    const trimmed = bindTarget.trim();
    if (!trimmed || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/auth/code/send', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: trimmed, purpose: kind === 'email' ? 'bind_email' : 'bind_phone' }),
      });
      if (!res.ok) {
        setError(res.status === 429
          ? copy('Please wait before requesting another code.', '请求过于频繁，请稍后再试。')
          : copy('Enter a valid contact address.', '请输入有效的邮箱或手机号。'));
        return;
      }
      setCooldown(60);
      setNotice(copy('Verification code sent.', '验证码已发送。'));
    } finally {
      setBusy(false);
    }
  };

  const submitBind = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/auth/account/bind', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: bindTarget.trim(), code: bindCode.trim() }),
      });
      if (!res.ok) {
        setError(res.status === 409
          ? copy('Already bound to another account.', '该邮箱或手机号已绑定其他账号。')
          : copy('Invalid or expired verification code.', '验证码无效或已过期。'));
        return;
      }
      const body = await res.json();
      setDetail(body.user);
      updateHostedSessionUser({ email: body.user.email, phone_masked: body.user.phone_masked });
      setActiveEditor(null);
      setBindTarget('');
      setBindCode('');
      setNotice(copy('Contact updated.', '绑定信息已更新。'));
    } finally {
      setBusy(false);
    }
  };

  const submitPasswordChange = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!isValidUserPassword(newPassword)) {
      setError(copy(
        'Password must be at least 8 characters and include uppercase, lowercase, and digits.',
        '新密码至少 8 位，且需包含大写字母、小写字母和数字。',
      ));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(copy('Passwords do not match.', '两次输入的新密码不一致。'));
      return;
    }
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/auth/account/password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(detail?.has_password ? { current_password: currentPassword } : {}),
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        setError(res.status === 401
          ? copy('Current password is incorrect.', '当前密码不正确。')
          : copy('Could not update password.', '密码更新失败。'));
        return;
      }
      const body = await res.json();
      setDetail(body.user);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setActiveEditor(null);
      setNotice(copy('Password updated; other devices were signed out.', '密码已更新，其他设备会话已自动下线。'));
    } finally {
      setBusy(false);
    }
  };

  const revokeOthers = async () => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/auth/account/sessions/revoke-others', {
        method: 'POST',
        credentials: 'same-origin',
      });
      if (res.ok) {
        const body = await res.json();
        setDetail(prev => prev ? { ...prev, active_sessions: body.active_sessions } : prev);
        setNotice(copy('Signed out other devices.', '已下线其他登录设备。'));
      }
    } finally {
      setBusy(false);
    }
  };

  const daysLeft = detail?.session_expires_at
    ? Math.max(1, Math.ceil((detail.session_expires_at - Date.now() / 1000) / 86400))
    : (detail?.session_persistent ? 30 : 1);

  return (
    <Modal title={copy('Account settings', '账号设置')} className="hosted-account-modal" busy={busy} onClose={onClose}>
      <div className="hosted-modal-stack">
        <div className="hosted-modal-meta">
          <span style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.75rem', color: 'var(--ink-faint)' }}>
            {detail?.username ?? session?.user?.username}
          </span>
          <span className="hosted-badge hosted-badge--ok">
            <span className="hosted-badge-dot" />
            <span>
              {detail?.session_persistent
                ? copy(`Persistent · ${daysLeft}d left`, `持久会话 · 剩余 ${daysLeft} 天`)
                : copy('24h session', '会话有效期 · 24 小时')}
            </span>
          </span>
        </div>

        {notice && <div className="hosted-status-note" role="status">{notice}</div>}
        {error && <div className="ui-error" role="alert">{error}</div>}

        <div>
          <div className="hosted-setting-row">
            <span className="hosted-setting-key">{copy('Display name', '显示名称')}</span>
            <div style={{ display: 'flex', gap: '8px', flex: 1 }}>
              <input
                type="text"
                maxLength={128}
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                disabled={busy}
                style={{ flex: 1, padding: '6px 10px', borderRadius: '8px', border: '1px solid var(--line-strong)', background: 'var(--surface-raised)', color: 'var(--ink)' }}
              />
              <button type="button" className="secondary-button" disabled={busy} onClick={() => { void saveDisplayName(); }}>
                {copy('Save', '保存')}
              </button>
            </div>
          </div>

          <div className="hosted-setting-row">
            <span className="hosted-setting-key">{copy('Email', '邮箱')}</span>
            <span className="hosted-setting-val">{detail?.email || copy('Not bound', '未绑定')}</span>
            {canVerify && (
              <button
                type="button"
                className="hosted-text-link"
                onClick={() => { setError(''); setBindTarget(''); setBindCode(''); setActiveEditor(activeEditor === 'email' ? null : 'email'); }}
              >
                {detail?.email ? copy('Change', '更换') : copy('Bind', '绑定')}
              </button>
            )}
          </div>

          <div className="hosted-setting-row">
            <span className="hosted-setting-key">{copy('Phone', '手机号')}</span>
            <span className="hosted-setting-val">{detail?.phone_masked || copy('Not bound', '未绑定')}</span>
            {canVerify && (
              <button
                type="button"
                className="hosted-text-link"
                onClick={() => { setError(''); setBindTarget(''); setBindCode(''); setActiveEditor(activeEditor === 'phone' ? null : 'phone'); }}
              >
                {detail?.phone_masked ? copy('Change', '更换') : copy('Bind', '绑定')}
              </button>
            )}
          </div>

          <div className="hosted-setting-row">
            <span className="hosted-setting-key">{copy('Password', '登录密码')}</span>
            <span className="hosted-setting-val">{detail?.has_password ? copy('Configured', '已设置') : copy('Not set', '未设置')}</span>
            <button
              type="button"
              className="hosted-text-link"
              onClick={() => { setError(''); setActiveEditor(activeEditor === 'password' ? null : 'password'); }}
            >
              {detail?.has_password ? copy('Change password', '修改密码') : copy('Set password', '设置密码')}
            </button>
          </div>

          <div className="hosted-setting-row">
            <span className="hosted-setting-key">{copy('Devices', '登录设备')}</span>
            <span className="hosted-setting-val">
              {copy(`${detail?.active_sessions ?? 1} active session(s)`, `当前共 ${detail?.active_sessions ?? 1} 台设备保持登录`)}
            </span>
            {(detail?.active_sessions ?? 1) > 1 && (
              <button type="button" className="hosted-text-link" style={{ color: 'var(--danger)' }} onClick={() => { void revokeOthers(); }}>
                {copy('Sign out others', '下线其他设备')}
              </button>
            )}
          </div>
        </div>

        {(activeEditor === 'email' || activeEditor === 'phone') && (
          <form className="hosted-inline-form" onSubmit={e => { void submitBind(e); }}>
            <label className="hosted-field">
              <span>{activeEditor === 'email' ? copy('New email', '新邮箱地址') : copy('New phone number', '新手机号')}</span>
              <input
                type={activeEditor === 'email' ? 'email' : 'tel'}
                required
                placeholder={activeEditor === 'email' ? 'name@example.com' : copy('11-digit phone number', '输入 11 位手机号')}
                value={bindTarget}
                onChange={e => setBindTarget(e.target.value)}
                disabled={busy}
              />
            </label>
            <div className="hosted-code-row">
              <input
                type="text"
                inputMode="numeric"
                maxLength={6}
                required
                placeholder={copy('6-digit code', '6 位验证码')}
                value={bindCode}
                onChange={e => setBindCode(e.target.value)}
                disabled={busy}
              />
              <button
                type="button"
                className="secondary-button"
                disabled={busy || cooldown > 0}
                onClick={() => { void sendBindCode(activeEditor); }}
              >
                {cooldown > 0 ? `${cooldown}s` : copy('Send code', '获取验证码')}
              </button>
            </div>
            <div className="hosted-inline-actions">
              <button type="button" className="secondary-button" onClick={() => setActiveEditor(null)}>{copy('Cancel', '取消')}</button>
              <button type="submit" className="primary-button" disabled={busy}>{copy('Confirm', '确认绑定')}</button>
            </div>
          </form>
        )}

        {activeEditor === 'password' && (
          <form className="hosted-inline-form" onSubmit={e => { void submitPasswordChange(e); }}>
            {detail?.has_password && (
              <ModalPasswordField
                label={copy('Current password', '当前密码')}
                value={currentPassword}
                onChange={setCurrentPassword}
                disabled={busy}
              />
            )}
            <ModalPasswordField
              label={copy('New password', '新密码')}
              minLength={8}
              placeholder={copy('8+ chars with upper, lower & digit', '至少 8 位，含大小写字母和数字')}
              value={newPassword}
              onChange={setNewPassword}
              disabled={busy}
            />
            <ModalPasswordField
              label={copy('Confirm new password', '确认新密码')}
              minLength={8}
              placeholder={copy('Re-enter new password', '再次输入新密码')}
              value={confirmPassword}
              onChange={setConfirmPassword}
              disabled={busy}
            />
            <div className="hosted-inline-actions">
              <button type="button" className="secondary-button" onClick={() => setActiveEditor(null)}>{copy('Cancel', '取消')}</button>
              <button type="submit" className="primary-button" disabled={busy}>{copy('Update password', '更新密码')}</button>
            </div>
          </form>
        )}
      </div>
    </Modal>
  );
}

export function AdminUsersModal({ onClose }: { onClose: () => void }) {
  const zh = useLocale(s => s.locale) === 'zh-CN';
  const copy = (en: string, zhText: string) => (zh ? zhText : en);
  const currentUserId = currentHostedSession()?.user?.id;

  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [creating, setCreating] = useState(false);

  const [newUsername, setNewUsername] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPhone, setNewPhone] = useState('');
  const [newRole, setNewRole] = useState<'user' | 'admin'>('user');
  const [newPwd, setNewPwd] = useState('');
  const [newPwdConfirm, setNewPwdConfirm] = useState('');

  const loadUsers = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set('q', query.trim());
      if (statusFilter !== 'all') params.set('status', statusFilter);
      const res = await fetch(`/hosting/admin/users?${params.toString()}`, {
        cache: 'no-store',
        credentials: 'same-origin',
      });
      if (!res.ok) return;
      const body = await res.json();
      setUsers(body.users ?? []);
    } catch {
      setError(copy('Could not load user directory.', '加载用户列表失败。'));
    }
  }, [query, statusFilter]);

  useEffect(() => { void loadUsers(); }, [loadUsers]);

  const updateUser = async (userId: string, patch: { enabled?: boolean; role?: 'user' | 'admin' }) => {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch(`/hosting/admin/users/${userId}`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        setError(body?.error?.message || copy('Update rejected.', '操作被拒绝。'));
        return;
      }
      const body = await res.json();
      setUsers(prev => prev.map(u => (u.id === userId ? body.user : u)));
      setNotice(patch.enabled === false
        ? copy('Account disabled and active sessions revoked.', '已禁用该账号并吊销其活跃会话。')
        : copy('User updated.', '用户状态已更新。'));
    } finally {
      setBusy(false);
    }
  };

  const handleCreateUser = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!/^[a-zA-Z][a-zA-Z0-9_.-]{1,31}$/.test(newUsername.trim())) {
      setError(copy(
        'Username must start with a letter and contain 2–32 letters, digits, ., _, or -.',
        '用户名需以字母开头，由 2–32 位字母、数字或 . _ - 组成。',
      ));
      return;
    }
    if (!isValidUserPassword(newPwd)) {
      setError(copy(
        'Password must be at least 8 characters and include uppercase, lowercase, and digits.',
        '密码至少 8 位，且需包含大写字母、小写字母和数字。',
      ));
      return;
    }
    if (newPwd !== newPwdConfirm) {
      setError(copy('Passwords do not match.', '两次输入的密码不一致。'));
      return;
    }
    setBusy(true); setError(''); setNotice('');
    try {
      const res = await fetch('/hosting/admin/users', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: newUsername.trim(),
          email: newEmail.trim() || undefined,
          phone: newPhone.trim() || undefined,
          password: newPwd,
          role: newRole,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        if (res.status === 409 && body?.error?.code === 'username_registered') {
          setError(copy('This username is already taken.', '该用户名已被占用，请更换一个。'));
        } else {
          setError(body?.error?.message || copy('Could not create user.', '创建用户失败，请检查用户名、邮箱或手机号是否重复。'));
        }
        return;
      }
      setCreating(false);
      setNewUsername(''); setNewEmail(''); setNewPhone(''); setNewPwd(''); setNewPwdConfirm('');
      setNotice(copy('User created.', '已创建新用户。'));
      await loadUsers();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={copy('User management', '用户管理')} className="hosted-admin-modal" busy={busy} onClose={onClose}>
      <div className="hosted-modal-stack">
        <div className="hosted-admin-toolbar">
          <div className="hosted-admin-filters">
            <input
              type="search"
              placeholder={copy('Search email, phone, username…', '搜索邮箱 / 手机号 / 用户名…')}
              value={query}
              onChange={e => setQuery(e.target.value)}
              style={{ width: '240px' }}
            />
            <select
              aria-label={copy('Filter by status', '按状态筛选')}
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as 'all' | 'enabled' | 'disabled')}
              style={{ minWidth: '112px' }}
            >
              <option value="all">{copy('All status', '全部状态')}</option>
              <option value="enabled">{copy('Enabled', '正常')}</option>
              <option value="disabled">{copy('Disabled', '已禁用')}</option>
            </select>
            <span className="hosted-badge">{copy(`${users.length} users`, `${users.length} 位用户`)}</span>
          </div>
          <button type="button" className="primary-button" onClick={() => { setError(''); setCreating(v => !v); }}>
            {creating ? copy('Close form', '收起') : copy('New user', '新建用户')}
          </button>
        </div>

        {notice && <div className="hosted-status-note" role="status">{notice}</div>}
        {error && <div className="ui-error" role="alert">{error}</div>}

        {creating && (
          <form className="hosted-inline-form" onSubmit={e => { void handleCreateUser(e); }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '10px' }}>
              <label className="hosted-field">
                <span>{copy('Username', '用户名')}</span>
                <input
                  type="text"
                  required
                  minLength={2}
                  maxLength={32}
                  placeholder={copy('Unique username', '唯一用户名')}
                  value={newUsername}
                  onChange={e => setNewUsername(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="hosted-field">
                <span>{copy('Email', '邮箱')}</span>
                <input type="email" placeholder="name@example.com" value={newEmail} onChange={e => setNewEmail(e.target.value)} disabled={busy} />
              </label>
              <label className="hosted-field">
                <span>{copy('Phone', '手机号')}</span>
                <input type="tel" placeholder={copy('11-digit mobile', '11 位手机号')} value={newPhone} onChange={e => setNewPhone(e.target.value)} disabled={busy} />
              </label>
              <label className="hosted-field">
                <span>{copy('Role', '角色')}</span>
                <select value={newRole} onChange={e => setNewRole(e.target.value as 'user' | 'admin')} disabled={busy}>
                  <option value="user">{copy('User', '普通用户')}</option>
                  <option value="admin">{copy('Administrator', '管理员')}</option>
                </select>
              </label>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '10px' }}>
              <ModalPasswordField
                label={copy('Password', '设置密码')}
                minLength={8}
                placeholder={copy('8+ chars with upper, lower & digit', '至少 8 位，含大小写字母和数字')}
                value={newPwd}
                onChange={setNewPwd}
                disabled={busy}
              />
              <ModalPasswordField
                label={copy('Confirm password', '确认密码')}
                minLength={8}
                placeholder={copy('Re-enter password', '再次输入密码')}
                value={newPwdConfirm}
                onChange={setNewPwdConfirm}
                disabled={busy}
              />
            </div>
            <div className="hosted-inline-actions">
              <button type="button" className="secondary-button" onClick={() => setCreating(false)}>{copy('Cancel', '取消')}</button>
              <button type="submit" className="primary-button" disabled={busy}>{copy('Create', '创建')}</button>
            </div>
          </form>
        )}

        <div className="hosted-table-wrap">
          <table className="hosted-admin-table">
            <thead>
              <tr>
                <th>{copy('Username', '用户名')}</th>
                <th>{copy('Email', '邮箱')}</th>
                <th>{copy('Phone', '手机号')}</th>
                <th>{copy('Workspace instance', '独立实例')}</th>
                <th>{copy('Role', '角色')}</th>
                <th>{copy('Status', '状态')}</th>
                <th style={{ textAlign: 'right' }}>{copy('Action', '操作')}</th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => {
                const isSelf = u.id === currentUserId;
                return (
                  <tr key={u.id} className={u.enabled ? '' : 'is-disabled'}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{u.username}</span>
                      {u.display_name && u.display_name !== u.username && (
                        <span style={{ marginLeft: '6px', fontSize: '0.75rem', color: 'var(--ink-faint)' }}>
                          ({u.display_name})
                        </span>
                      )}
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.78rem' }}>{u.email || '—'}</td>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.78rem' }}>{u.phone_masked || '—'}</td>
                    <td style={{ fontSize: '0.78rem', color: 'var(--ink-soft)' }}>
                      {u.instance
                        ? `${u.instance.name} · ${u.instance.mode === 'builder' ? 'Builder' : 'Runtime'}`
                        : u.provisioning
                          ? copy('Provisioning…', '初始化中…')
                          : copy('Unassigned', '未分配')}
                    </td>
                    <td>
                      <select
                        aria-label={copy(`Role for ${u.username}`, `${u.username} 角色`)}
                        value={u.role}
                        disabled={busy || isSelf}
                        onChange={e => { void updateUser(u.id, { role: e.target.value as 'user' | 'admin' }); }}
                      >
                        <option value="user">{copy('User', '普通用户')}</option>
                        <option value="admin">{copy('Administrator', '管理员')}</option>
                      </select>
                    </td>
                    <td>
                      <span className={`hosted-badge ${u.enabled ? 'hosted-badge--ok' : 'hosted-badge--off'}`}>
                        <span className="hosted-badge-dot" />
                        <span>{u.enabled ? copy('Enabled', '正常') : copy('Disabled', '已禁用')}</span>
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={busy || isSelf}
                        style={{
                          minHeight: '30px',
                          padding: '4px 11px',
                          fontSize: '0.75rem',
                          color: u.enabled ? 'var(--danger)' : 'var(--success)',
                        }}
                        onClick={() => { void updateUser(u.id, { enabled: !u.enabled }); }}
                      >
                        {u.enabled ? copy('Disable', '禁用') : copy('Enable', '启用')}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  );
}
