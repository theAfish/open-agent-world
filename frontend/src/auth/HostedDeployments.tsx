import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, RefreshCw } from 'lucide-react';
import { useLocale } from '../i18n';
import type { HostedSession } from './hostedSession';
import { adminRequest } from './adminRequest';

interface Profile { id: string; user_id: string; name: string; mode: 'builder' | 'runtime' }
interface User { id: string; username: string; role: string; enabled: boolean; entry_id: string | null }
interface Release { id: string; name: string; created_at: string }
interface Job { id: string; user_id: string; name: string; mode: string; status: string; message: string; created_at: number }
interface Catalog { releases: Release[]; release_error: string | null; users: User[]; profiles: Profile[]; jobs: Job[] }

export function HostedDeployments({ session }: { session: HostedSession }) {
  const { locale } = useLocale();
  const c = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [catalog, setCatalog] = useState<Catalog>();
  const [release, setRelease] = useState('');
  const [mode, setMode] = useState<'builder' | 'runtime'>('runtime');
  const [operation, setOperation] = useState<'update' | 'initialize'>('update');
  const [updateTargets, setUpdateTargets] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [entries, setEntries] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const attempt = useRef<{ payload: string; id: string }>();
  const reload = useCallback(async () => {
    try { setCatalog(await adminRequest<Catalog>('deployments')); }
    catch (cause) { setError((cause as Error).message); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  const pending = catalog?.jobs.some(job => ['queued', 'preparing_resources', 'waiting_resources', 'preparing'].includes(job.status));
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => { void reload(); }, 4000);
    return () => clearInterval(timer);
  }, [pending, reload]);
  const deploy = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      const targetProfiles = operation === 'update' ? Object.fromEntries(selected.map(id => [id,
        updateTargets[id] ?? catalog?.users.find(user => user.id === id)?.entry_id ?? ''])) : undefined;
      const definition = { release_id: release, user_ids: selected, mode: operation === 'update' ? 'runtime' : mode,
        ...(targetProfiles ? { target_profiles: targetProfiles } : {}) };
      const payload = JSON.stringify(definition);
      if (attempt.current?.payload !== payload) attempt.current = { payload, id: crypto.randomUUID() };
      await adminRequest('deployments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: attempt.current.id, ...definition }) });
      attempt.current = undefined;
      setSelected([]);
      setNotice(operation === 'update'
        ? c('Update queued. Compatible changes retain the workspace and its data; incompatible changes are rejected.', '更新已提交。兼容更新会保留工作区和数据；不兼容的更改会被拒绝。')
        : c('Deployment queued. When ready, select the new workspace below and set it as the account entry.', '部署已提交。准备完成后，在下方选择新工作区并设为账号入口。'));
    } catch (cause) { setError((cause as Error).message + c(' Refresh the deployment list before trying again.', ' 请先刷新部署记录，确认结果后再操作。')); }
    finally { await reload(); setBusy(false); }
  };
  const assign = async (user: User, profileId: string) => {
    setBusy(true); setError(''); setNotice('');
    try {
      await adminRequest('entry', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: user.id, profile_id: profileId }) });
      setNotice(c(`${user.username}'s entry has changed. They can refresh to enter; previous data is retained.`, `${user.username} 的入口已更新，刷新页面即可进入；之前的数据会保留。`));
    } catch (cause) { setError((cause as Error).message); }
    finally { await reload(); setBusy(false); }
  };
  const users = catalog?.users.filter(user => user.role !== 'admin') ?? [];
  const missingUpdateTarget = operation === 'update' && selected.some(id => !catalog?.profiles.some(profile =>
    profile.user_id === id && profile.mode === 'runtime' && profile.id ===
      (updateTargets[id] ?? users.find(user => user.id === id)?.entry_id)));
  const state = (value: string) => ({ queued: c('Queued', '排队中'), preparing: c('Preparing', '正在准备'), ready: c('Ready', '已就绪'),
    preparing_resources: c('Preparing cloud storage', '正在准备云存储'), waiting_resources: c('Waiting for cloud storage', '等待云存储就绪'),
    failed: c('Failed · operator review needed', '失败 · 需检查服务器'), interrupted: c('Interrupted · operator review needed', '中断 · 需检查服务器') }[value] ?? value);
  return <div className="hosted-deployments">
    <section className="hosted-admin-form">
      <h2>{c('Build your application', '搭建应用')}</h2>
      <p>{c('Use your engineering canvas to build a Legion, arrange its Workspace and create a release. Then deploy independent copies to user accounts.', '在自己的工程画布中搭建 Legion、编排 Workspace 并创建发布版本，再为使用者部署独立副本。')}</p>
      {session.instance?.mode === 'builder' ? <a className="primary-button" href="/">{c('Open engineering canvas', '打开工程画布')}<ArrowUpRight size={16} /></a>
        : <p role="status">{c('An operator must assign an engineering workspace to this administrator.', '请由部署人员为此管理员开通工程工作区。')}</p>}
    </section>
    <section className="hosted-admin-form">
      <div className="hosted-section-heading"><h2>{c('Deploy a release', '部署发布版本')}</h2><button className="secondary-button" disabled={busy} onClick={() => { setError(''); void reload(); }}><RefreshCw size={14} />{c('Refresh', '刷新')}</button></div>
      <p>{c('Update an existing Workspace to retain its files, documents and conversations. Create a private workspace for a new application or an incompatible structural change. Shared models and Sandbox settings apply to both.', '更新已有 Workspace 会保留文件、文档和对话。新应用或不兼容的结构变化请选择新建独立工作区。两者均继承共享模型和沙盒设置。')}</p>
      {catalog?.release_error && <p role="alert">{catalog.release_error}</p>}
      {!catalog ? <p role="status">{c('Loading…', '正在加载…')}</p> : <>
        <div className="hosted-deploy-fields">
          <label>{c('Deployment action', '部署操作')}<select value={operation} onChange={event => setOperation(event.target.value as 'update' | 'initialize')}>
            <option value="update">{c('Update existing Workspace', '更新已有工作区')}</option>
            <option value="initialize">{c('Create private workspace', '新建独立工作区')}</option>
          </select></label>
          <label>{c('Published version', '发布版本')}<select value={release} onChange={event => setRelease(event.target.value)}>
            <option value="">{c('Select a release', '选择发布版本')}</option>
            {[...catalog.releases].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(item => <option key={item.id} value={item.id}>{item.name} · {new Date(item.created_at).toLocaleString()} · {item.id.slice(0, 8)}</option>)}
          </select></label>
          {operation === 'initialize' && <label>{c('User interface', '用户界面')}<select value={mode} onChange={event => setMode(event.target.value as 'builder' | 'runtime')}>
            <option value="runtime">{c('Workspace only', '仅 Workspace')}</option><option value="builder">{c('Canvas · testing', '画布 · 测试使用')}</option>
          </select></label>}
        </div>
        {!catalog.releases.length && <p>{c('No releases yet. Save a Workspace layout, then use Publish application in the Legion.', '还没有发布版本。先保存 Workspace 布局，再从 Legion 中点击“发布应用”。')}</p>}
        <fieldset className="hosted-targets"><legend>{c('Target accounts', '使用账号')}</legend>
          {users.map(user => <div key={user.id}><label><input type="checkbox" disabled={!user.enabled || busy} checked={selected.includes(user.id)}
            onChange={event => setSelected(current => event.target.checked ? [...current, user.id] : current.filter(id => id !== user.id))} />{user.username}{!user.enabled && c(' · disabled', ' · 已停用')}</label>
            {operation === 'update' && selected.includes(user.id) && <select aria-label={c(`${user.username} update target`, `${user.username} 更新目标`)}
              disabled={busy} value={updateTargets[user.id] ?? user.entry_id ?? ''} onChange={event => setUpdateTargets(current => ({ ...current, [user.id]: event.target.value }))}>
              <option value="">{c('Select a Workspace', '选择工作区')}</option>
              {catalog.profiles.filter(profile => profile.user_id === user.id && profile.mode === 'runtime').map(profile =>
                <option key={profile.id} value={profile.id}>{profile.name} · {profile.id.slice(0, 8)}</option>)}
            </select>}</div>)}
        </fieldset>
        <button className="primary-button" disabled={busy || !release || !selected.length || pending || missingUpdateTarget} onClick={() => { void deploy(); }}>{busy ? c('Submitting…', '正在提交…') : operation === 'update' ? c('Update selected workspaces', '更新所选工作区') : c('Create private workspaces', '部署独立工作区')}</button>
      </>}
      {error && <p className="settings-error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </section>
    {catalog && <section className="hosted-admin-form">
      <h2>{c('Account entry', '账号入口')}</h2>
      <p>{c('Set the workspace shown after sign-in. Switching retains earlier data and background tasks; open browser tabs return to the new entry. Switch back here when needed.', '选择用户登录后进入的工作区。切换会保留旧数据和后台任务，已打开的页面会返回新入口；需要时可在此切回。')}</p>
      <div className="hosted-entries">{users.map(user => {
        const choices = catalog.profiles.filter(profile => profile.user_id === user.id);
        const chosen = entries[user.id] ?? user.entry_id ?? '';
        return <div className="hosted-entry" key={user.id}><strong>{user.username}</strong>
          <select aria-label={c(`${user.username} workspace`, `${user.username} 工作区`)} value={chosen} onChange={event => setEntries(current => ({ ...current, [user.id]: event.target.value }))}>
            <option value="" disabled>{c('No workspace assigned', '尚未分配工作区')}</option>
            {choices.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.mode === 'runtime' ? 'Workspace' : c('Canvas', '画布')} · {profile.id.slice(0, 8)}{profile.id === user.entry_id ? c(' · current', ' · 当前入口') : ''}</option>)}
          </select><button className="secondary-button" disabled={busy || !user.enabled || !chosen || chosen === user.entry_id} onClick={() => { void assign(user, chosen); }}>{c('Set entry', '设为入口')}</button></div>;
      })}</div>
    </section>}
    {!!catalog?.jobs.length && <section className="hosted-admin-form"><h2>{c('Deployment history', '部署记录')}</h2>
      <ul className="hosted-job-list">{catalog.jobs.map(job => <li key={job.id}><div><strong>{job.name}</strong><small>{catalog.users.find(user => user.id === job.user_id)?.username ?? job.user_id} · {job.mode === 'runtime' ? 'Workspace' : c('Canvas', '画布')} · {new Date(job.created_at * 1000).toLocaleString()}</small>{job.message && <small>{job.message}</small>}</div><span>{state(job.status)}</span></li>)}</ul>
    </section>}
  </div>;
}
