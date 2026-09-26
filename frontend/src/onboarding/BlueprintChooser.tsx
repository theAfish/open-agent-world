import { Bot, Code2, Layers3, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { apiErrorMessage, worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import { useWorldStore } from '../state/worldStore';
import type { LegionSummary } from '../types/world';
import { tutorial, useTutorialStore } from './controller';

const icons = { assistant: Bot, coding: Code2, team: Users };

export function BlueprintChooser() {
  useLocale();
  const [presets, setPresets] = useState<LegionSummary[]>([]);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [goal, setGoal] = useState('');
  const [selected, setSelected] = useState('assistant');
  const legions = useWorldStore(s => s.legions);
  const saved = legions.filter(item => !item.preset);
  const offline = useWorldStore(s => s.syncState === 'offline');
  const busy = useTutorialStore(s => s.busy);
  useEffect(() => {
    let active = true;
    setError('');
    worldApi.getBlueprintPresets().then(items => { if (active) setPresets(items.filter(item => item.starter)); })
      .catch(e => { if (active) setError(apiErrorMessage(e)); });
    return () => { active = false; };
  }, [retry]);
  const deploy = (blueprint: LegionSummary, preset: boolean) => void tutorial.fromBlueprint(blueprint, preset, goal.trim());
  const chosen = presets.find(item => item.id === selected && item.compatible);
  return <div className="blueprint-chooser">
    <h2>{t('What would you like to do here?')}</h2>
    <p>{t('Describe your goal or choose a starting workspace. You can change it later.')}</p>
    <form className="blueprint-goal" onSubmit={event => { event.preventDefault(); if (chosen) deploy(chosen, true); }}>
      <label htmlFor="first-goal" className="sr-only">{t('What would you like help with?')}</label>
      <textarea id="first-goal" value={goal} maxLength={10000} rows={2} disabled={busy} onChange={event => setGoal(event.target.value)}
        placeholder={t('For example: help me work on a code project')} />
    <div className="blueprint-grid">
      {presets.map(item => {
        const Icon = icons[item.id as keyof typeof icons] ?? Layers3;
        return <button type="button" key={item.id} className="blueprint-option" aria-pressed={selected === item.id} disabled={busy || offline || !item.compatible}
          title={item.compatible ? undefined : item.issues.join(' ')} onClick={() => setSelected(item.id)}>
          <Icon size={21} /><strong>{t(item.name)}</strong><span>{t(item.description ?? '')}</span>
        </button>;
      })}
    </div>
    <button className="primary-button blueprint-start" type="submit" disabled={!chosen || busy || offline}>{t(busy ? 'Preparing workspace…' : 'Open workspace')}</button>
    <small>{t('Your goal will be placed in the conversation for you to review and send.')}</small>
    </form>
    {!presets.length && !error && <p role="status">{t('Loading blueprints…')}</p>}
    {error && <p className="onboarding-error" role="alert">{error} <button className="onboarding-text-button" onClick={() => setRetry(v => v + 1)}>{t('Retry')}</button></p>}
    {saved.length > 0 && <details className="blueprint-saved"><summary>{t('Use a saved Legion as a blueprint')}</summary>
      <p>{t('Create a new workspace from a saved Legion. The saved template is kept.')}</p>
      {saved.map(item => <button key={item.id} className="secondary-button" disabled={busy || offline || !item.compatible}
        title={item.issues.join(' ')} onClick={() => deploy(item, false)}><Layers3 size={14} />{item.name}</button>)}
    </details>}
  </div>;
}
