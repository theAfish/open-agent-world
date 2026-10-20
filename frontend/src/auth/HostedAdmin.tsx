import { useEffect, useState, type FormEvent } from 'react';
import { LogOut, ShieldCheck } from 'lucide-react';
import { useLocale } from '../i18n';
import { ModelConnectionsEditor } from '../shell/ModelConnectionsEditor';
import { EMPTY_MODEL_CATALOG, type ModelCatalog, type ModelConnection } from '../state/modelConnections';
import { signOutHosted, type HostedSession } from './hostedSession';
import { adminRequest } from './adminRequest';
import { HostedDeployments } from './HostedDeployments';
import { SettingsPanel } from '../shell/SettingsPanel';
import { useWorldStore } from '../state/worldStore';
import './hosted.css';

interface HostingStatus { shared_models_enabled: boolean; shared_environment_enabled?: boolean; workspace_count: number; users: { username: string; display_name: string; role: string; enabled: boolean }[] }
type AdminTab = 'deployments' | 'models' | 'accounts';

export function HostedAdmin({ session }: { session: HostedSession }) {
  const { locale, setLocale } = useLocale();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const allowed = session.user?.role === 'admin';
  const [tab, setTab] = useState<AdminTab>(() => {
    const section = new URLSearchParams(window.location.search).get('section');
    return section === 'models' || section === 'accounts' ? section : 'deployments';
  });
  useEffect(() => {
    if (allowed && new URLSearchParams(window.location.search).get('section') === 'environment') {
      useWorldStore.setState({ settingsOpen: true });
    }
  }, [allowed]);
  const [draft, setDraft] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [saved, setSaved] = useState<ModelCatalog>(EMPTY_MODEL_CATALOG);
  const [status, setStatus] = useState<HostingStatus>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!allowed) return;
    let active = true;
    setLoading(true); setError(''); setNotice(false);
    Promise.all([adminRequest<ModelCatalog>('models'), adminRequest<HostingStatus>('status')]).then(([catalog, info]) => {
      if (active) { setDraft(catalog); setSaved(catalog); setStatus(info); setLoading(false); }
    }).catch(cause => { if (active) { setError(cause instanceof Error ? cause.message : 'Could not load shared settings.'); setLoading(false); } });
    return () => { active = false; };
  }, [allowed, reload]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || loading || !status) return;
    setBusy(true); setError(''); setNotice(false);
    try {
      const catalog = await adminRequest<ModelCatalog>('models', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
      setDraft(catalog); setSaved(catalog); setNotice(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save shared settings.'); }
    finally { setBusy(false); }
  };
  const discoverModels = (connection: ModelConnection, signal?: AbortSignal) => adminRequest<{ models: { id: string; name: string }[]; truncated: boolean }>(
    'models/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(connection), signal });
  return <main className="hosted-admin">
    <header className="hosted-admin-header"><div><ShieldCheck size={24} /><div><small>Open Agent World</small><h1>{copy('Administration', '管理后台')}</h1></div></div>
      <div><select aria-label={copy('Language', '语言')} value={locale} onChange={event => setLocale(event.target.value === 'zh-CN' ? 'zh-CN' : 'en')}><option value="zh-CN">简体中文</option><option value="en">English</option></select>
        {session.instance && <a href="/">{copy('My workspace', '我的工作区')}</a>}
        <button className="secondary-button" onClick={() => { void signOutHosted(); }}><LogOut size={15} />{copy('Sign out', '退出登录')}</button></div>
    </header>
    {!allowed ? <p role="alert">{copy('Administrator access required.', '需要管理员权限。')}</p> : <>
      <nav className="hosted-admin-nav" aria-label={copy('Administration sections', '管理分类')}>
        {(['deployments', 'models', 'accounts'] as const).map(item => <button key={item} className={tab === item ? 'primary-button' : 'secondary-button'} aria-pressed={tab === item} onClick={() => setTab(item)}>{item === 'deployments' ? copy('Build & deploy', '搭建与部署') : item === 'models' ? copy('Shared models', '公共模型') : copy('Accounts', '账号')}</button>)}
        <button type="button" className="secondary-button" onClick={() => useWorldStore.setState({ settingsOpen: true })}>{copy('Settings', '设置')}</button>
      </nav>
      {tab === 'deployments' && <HostedDeployments session={session} />}
      <SettingsPanel initialSection="sandbox" />
      {tab !== 'models' && error && <p className="settings-error" role="alert">{error}</p>}
      {tab === 'models' && <>
      <section className="hosted-admin-intro"><h2>{copy('Shared models', '公共模型')}</h2>
        <p>{copy('Configure a connection, add models, choose a default, then save. Users can use these models without entering their own keys.', '配置连接、添加模型并选择默认模型后保存。用户无需填写自己的密钥，即可使用这些模型。')}</p>
        <small>{copy('Saved keys are never displayed. Changes apply to subsequent model calls; open workspaces refresh the list automatically.', '已保存的密钥不会显示。修改作用于后续模型调用，已打开的工作区会自动刷新模型列表。')}</small>
        {status && <p>{copy(`${status.workspace_count} workspaces use this deployment.`, `当前部署包含 ${status.workspace_count} 个工作区。`)} {!status.shared_models_enabled && copy('Shared models are not enabled by the deployment operator yet.', '部署端尚未开启公共模型继承。')}</p>}
      </section>
      <form className="hosted-admin-form" onSubmit={submit}>
        {loading ? <p role="status">{copy('Loading shared settings…', '正在加载公共配置…')}</p> : status && <ModelConnectionsEditor value={draft} saved={saved} busy={busy} allowEnvironment={false} discoverModels={discoverModels} onChange={value => { setDraft(value); setNotice(false); }} />}
        {error && <p className="settings-error" role="alert">{error}</p>}
        {notice && <p role="status">{copy('Shared settings saved. Users can use the enabled models.', '公共配置已保存，用户可以使用已启用的模型。')}</p>}
        <footer><button type="button" className="secondary-button" disabled={busy || loading} onClick={() => setReload(value => value + 1)}>{copy('Discard draft and reload', '放弃草稿并重新加载')}</button>
          <button className="primary-button" type="submit" disabled={busy || loading || !status}>{busy ? copy('Saving…', '正在保存…') : copy('Save shared models', '保存公共模型')}</button></footer>
      </form>
      </>}
      {tab === 'accounts' && status && <section className="hosted-admin-users"><h2>{copy('Accounts', '账号')}</h2><p>{copy('Accounts and administrator roles are assigned by the deployment operator. Manage workspace access in Build & deploy.', '账号及管理员权限由部署人员开通和分配，工作区入口可在“搭建与部署”中管理。')}</p><ul>{status.users.map(user => <li key={user.username}><strong>{user.username}</strong><span>{user.role === 'admin' ? copy('Administrator', '管理员') : copy('User', '用户')}{!user.enabled && (' · ' + copy('Disabled', '已停用'))}</span></li>)}</ul></section>}
    </>}
  </main>;
}
