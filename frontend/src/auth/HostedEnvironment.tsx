import { useEffect, useState, type FormEvent } from 'react';
import { useLocale } from '../i18n';
import { EnvironmentVariablesEditor, environmentSecretUpdates, environmentVariablesFromValue,
  environmentVariablesToValue, type EnvironmentVariableRow } from '../cards/ExecutionConfiguration';
import { adminRequest } from './adminRequest';
import '../cards/executionConfiguration.css';

type EnvironmentSettings = {
  revision: number;
  environment_variables: Record<string, string | { secret_ref: string }>;
  secret_bindings: Record<string, boolean>;
};

export function HostedEnvironment({ enabled }: { enabled?: boolean }) {
  const locale = useLocale(state => state.locale);
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const [saved, setSaved] = useState<EnvironmentSettings>();
  const [rows, setRows] = useState<EnvironmentVariableRow[]>([]);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(false);
  const [reload, setReload] = useState(0);
  const accept = (value: EnvironmentSettings) => {
    setSaved(value);
    setRows(environmentVariablesFromValue({ variables: value.environment_variables }));
    setSecrets({});
  };
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setNotice(false); setSecrets({});
    void adminRequest<EnvironmentSettings>('environment').then(value => {
      if (active) accept(value);
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : 'Could not load environment settings.');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reload]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!saved || busy || loading) return;
    setBusy(true); setError(''); setNotice(false);
    try {
      const variables = environmentVariablesToValue(rows).variables;
      const updates = environmentSecretUpdates(rows, secrets, saved.secret_bindings);
      const value = await adminRequest<EnvironmentSettings>('environment', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: saved.revision, environment_variables: variables, secrets: updates }),
      });
      accept(value); setNotice(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save environment settings.');
    } finally { setBusy(false); }
  };
  return <>
    <section className="hosted-admin-intro"><h2>{copy('Global environment variables', '全局环境变量')}</h2>
      <p>{copy('Configure tool service parameters and keys once for all workspaces. Existing and new Sandboxes use them on subsequent commands.',
        '统一配置工具服务参数和密钥。已有和新建工作区的 Sandbox 都会在后续命令中使用，无需逐个配置。')}</p>
      <small>{copy('Workspace and card values take priority. Running commands keep their current environment. Saved secrets are hidden in settings and redacted from ordinary command output, but Sandbox code can use them. Only share credentials intended for every user.',
        '工作区和卡片中的同名配置优先，正在运行的命令保持原环境。密钥在设置中隐藏，常规命令输出会脱敏，但 Sandbox 代码可以使用它们；仅填写允许所有用户共用的服务凭据。')}</small>
      {enabled === false && <p role="status">{copy('Settings can be saved, but shared environment delivery has not been enabled by the deployment operator.',
        '配置可以保存，但部署端尚未启用公共环境下发。')}</p>}
    </section>
    <form className="hosted-admin-form hosted-environment-form" onSubmit={submit}>
      {loading ? <p role="status">{copy('Loading environment settings…', '正在加载环境配置…')}</p> : saved && (
        <EnvironmentVariablesEditor rows={rows} disabled={busy} allowPaths={false} bindings={saved.secret_bindings}
          secrets={secrets} onSecretsChange={value => { setSecrets(value); setNotice(false); }}
          onChange={value => { setRows(value); setNotice(false); }} />)}
      {error && <p className="settings-error" role="alert">{error}</p>}
      <footer>
        {notice && <span role="status">{copy('Environment settings saved.', '环境配置已保存。')}</span>}
        <button type="button" className="secondary-button" disabled={busy || loading}
          onClick={() => setReload(value => value + 1)}>{copy('Discard draft and reload', '放弃草稿并重新加载')}</button>
        <button type="submit" className="primary-button" disabled={busy || loading || !saved}>
          {busy ? copy('Saving…', '正在保存…') : copy('Save environment variables', '保存环境变量')}</button>
      </footer>
    </form>
  </>;
}
