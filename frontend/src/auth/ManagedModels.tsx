import { useLocale } from '../i18n';
import { availableModels, type ModelCatalog } from '../state/modelConnections';

export function ManagedModels({ catalog }: { catalog: ModelCatalog }) {
  const { locale } = useLocale();
  const copy = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const models = availableModels(catalog);
  return <section className="managed-models">
    <h3>{copy('Shared models', '公共模型')}</h3>
    <p>{copy('Your administrator manages model connections and keys. Choose a model on an Agent, or use the shared default.', '模型连接和密钥由管理员统一配置。你可以在 Agent 中选择模型，或使用公共默认模型。')}</p>
    {models.length ? <ul>{models.map(model => <li key={model.value}><strong>{model.label}</strong><span>{model.connection}{model.value === catalog.default_model && (' · ' + copy('Default', '默认'))}</span></li>)}</ul>
      : <p role="status">{copy('No shared models are available yet. Ask your administrator to configure one.', '暂无可用的公共模型，请联系管理员完成配置。')}</p>}
  </section>;
}
