import { t } from '../i18n';
import type { PackBackendLibrary, PackEnvironmentStatus, PackInstallationPlan, PackInventoryItem } from '../types/packs';
import './packDependencies.css';

const states: Record<PackEnvironmentStatus['state'], string> = {
  ready: 'Ready', missing: 'Not prepared', on_use: 'Prepared when used', needs_configuration: 'Needs configuration',
  unsupported: 'Unsupported', preparing: 'Preparing', failed: 'Failed',
};
const sources = { archive: 'Local file', store: 'Store', bundled: 'Bundled', development: 'Development source', installed: 'Installed' };
const dependencyStates = { satisfied: 'Installed', missing: 'Missing', incompatible: 'Incompatible', disabled: 'Disabled' };

function ApplicationDependencies({ libraries = [], backend = [], frontend = [] }: {
  libraries?: PackBackendLibrary[]; backend?: string[]; frontend?: string[];
}) {
  if (!libraries.length && !backend.length && !frontend.length) return null;
  const host = libraries.filter(library => library.management === 'host-provided');
  const managed = libraries.filter(library => library.management === 'managed');
  const names = (items: PackBackendLibrary[]) => items.map(item => `${item.name}${item.version ? ` ${item.version}` : ''}`).join(', ');
  return <details>
    <summary>{t('Application dependencies')}</summary>
    {host.length > 0 && <div><p><b>{t('Provided by OAW')}</b> · {names(host)}</p>
      <p>{t('Kept when this Pack is uninstalled.')}</p></div>}
    {managed.length > 0 && <div><p><b>{t('Shared Pack libraries')}</b> · {names(managed)}</p>
      <p>{t('Removed when no Pack or running process needs them.')}</p></div>}
    {backend.length > 0 && <p><b>{t('Backend')}</b> · {backend.join(', ')}</p>}
    {frontend.length > 0 && <p><b>{t('Frontend')}</b> · {frontend.join(', ')}</p>}
  </details>;
}

export function PackEnvironments({ environments }: { environments: PackEnvironmentStatus[] }) {
  if (!environments.length) return null;
  return <div className="pack-environments" aria-label={t('Runtime environments')}>
    {environments.map(environment => <div className="pack-environment" key={`${environment.target}:${environment.id}`} data-state={environment.state}>
      <div className="pack-environment-heading"><strong>{environment.id}</strong><span>{t(states[environment.state])}</span></div>
      <p>{t(environment.target === 'sandbox' ? 'Sandbox' : 'Pack engine')}
        {environment.management === 'legacy-shared' && ` · ${t('Shared environment')}`}</p>
      {environment.python.length + environment.node.length > 0 && <details><summary>{t('Dependencies')}</summary>
        {environment.python.length > 0 && <p><b>Python</b> · {environment.python.join(', ')}</p>}
        {environment.node.length > 0 && <p><b>Node</b> · {environment.node.join(', ')}</p>}
      </details>}
      {environment.error && <p className="pack-action-error" role="alert">{environment.error}</p>}
    </div>)}
  </div>;
}

export function PackPlanDetails({ plan }: { plan: PackInstallationPlan }) {
  return <section className="pack-dependencies" aria-label={t('Installation plan')}>
    <h4>{t('Installation plan')}</h4>
    <p>{t(sources[plan.source])} · {plan.version} · {t(plan.restart_required ? 'Restart required' : 'Available after installation')}</p>
    {plan.dependencies.length > 0 && <div><strong>{t('Pack dependencies')}</strong>
      <ul>{plan.dependencies.map(dependency => <li key={dependency.id}>
        {dependency.id} {dependency.version} · {t(dependencyStates[dependency.state])}
      </li>)}</ul></div>}
    <PackEnvironments environments={plan.environments} />
    <ApplicationDependencies libraries={plan.backend_libraries} />
    {plan.warnings.map((warning, index) => <p key={`warning:${index}`}>{warning}</p>)}
    {plan.blockers.map((blocker, index) => <p key={`blocker:${index}`} className="pack-action-error" role="alert">{blocker}</p>)}
  </section>;
}

export function PackRuntimeDetails({ inventory }: { inventory: PackInventoryItem }) {
  return <section className="pack-dependencies" aria-label={t('Pack dependencies')}>
    <p>{t(sources[inventory.source])} · {inventory.version} · {t(inventory.loaded ? 'Loaded' : 'Not loaded')}
      {!inventory.enabled && ` · ${t('Disabled')}`}</p>
    <PackEnvironments environments={inventory.environments} />
    <ApplicationDependencies libraries={inventory.backend_libraries}
      backend={inventory.backend_requirements} frontend={inventory.frontend_dependencies} />
    {inventory.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
  </section>;
}
