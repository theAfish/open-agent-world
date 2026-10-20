import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { CircleCheck, CircleHelp, CirclePlay, CircleSlash, CircleX, Clock3, LoaderCircle, Pause, Play, RefreshCw, Search, Server, Square, TriangleAlert, X, type LucideIcon } from 'lucide-react';
import { worldApi } from '../api/client';
import { IconButton } from '../components/IconButton';
import { t, useLocale } from '../i18n';
import { useCardStateSession } from '../state/cardState';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { WorkspaceEmptyState, WorkspaceToolbar } from '../workspace/WorkspacePrimitives';
import { jobGroup, jobStatusLabels, type BackgroundJob, type JobAction, type JobsHost, type JobsSnapshot } from './jobTypes';
import './jobs.css';

export function JobsCard({ card }: { card: WorldCard }) {
  const session = useCardStateSession(card.id);
  const { deployed, permissions } = useWorkspaceAccess();
  const allowed = !deployed || !!permissions[card.id]?.includes('jobs');
  // Any grant change can revoke access to a job's Sandbox or Conversation.
  const connections = useWorldStore(state => state.edges.map(e => `${e.id}:${e.source}:${e.target}:${e.relationship}:${e.direction}:${!!e.missing_plugin}`).sort().join('|'));
  const api = useMemo<JobsHost | undefined>(() => !allowed ? undefined : {
    list: () => worldApi.backgroundJobs(card.id, session ?? null),
    control: (job, action) => worldApi.controlBackgroundJob(card.id, job.id, action, job.revision, session ?? null),
  }, [card.id, session, allowed, connections]);
  return <JobsBoard key={`${card.id}:${session}:${connections}`} api={api} />;
}

function timestamp(value: number | null | undefined) {
  return value ? new Date(value * 1000).toLocaleString(useLocale.getState().locale) : t('Not observed yet');
}

const controls = {
  refresh: { icon: RefreshCw, label: 'Refresh job status' },
  pause: { icon: Pause, label: 'Pause monitoring', title: 'Pause monitoring; external work continues.' },
  resume: { icon: Play, label: 'Resume monitoring' },
  cancel: { icon: Square, label: 'Cancel external job' },
};

const statusIcons: Record<string, LucideIcon> = {
  submitting: LoaderCircle, queued: Clock3, running: CirclePlay, succeeded: CircleCheck,
  failed: CircleX, cancelled: CircleSlash, interrupted: TriangleAlert, unknown: CircleHelp,
};

function JobTile({ job, onAction, disabled }: { job: BackgroundJob; onAction: (action: JobAction) => Promise<void>; disabled: boolean }) {
  const detailsId = useId();
  const [expanded, setExpanded] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const StatusIcon = statusIcons[job.status] ?? CircleHelp;
  const statusLabel = t(jobStatusLabels[job.status] ?? 'Unknown');
  const monitorLabel = job.tracking_released ? t('Monitoring detached') : job.paused ? t('Monitoring paused') : '';
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function act(action: JobAction) {
    setBusy(true);
    try { await onAction(action); }
    finally { if (mounted.current) { setBusy(false); setConfirm(false); } }
  }
  return <article className="job-tile" data-status={job.status} data-group={jobGroup(job)} data-expanded={expanded} aria-label={job.request_key}>
    <button type="button" className="job-tile-toggle nodrag nopan" aria-label={t(expanded ? 'Hide job details' : 'Show job details')}
      aria-expanded={expanded} aria-controls={expanded ? detailsId : undefined} onClick={() => { setExpanded(!expanded); setConfirm(false); }}>
      <span className="job-tile-title" title={job.request_key}>{job.request_key}</span>
      <span className="job-tile-state">
        <span className="job-status"><StatusIcon size={12} aria-hidden="true" />{statusLabel}</span>
        {monitorLabel && <span className="job-monitor-state" title={monitorLabel}>{monitorLabel}</span>}
        {job.observation_error && <span className="job-observation-indicator" role="img" aria-label={t('Needs attention')} title={job.observation_error}>
          <TriangleAlert size={12} aria-hidden="true" />
        </span>}
      </span>
    </button>
    {expanded && <div className="job-tile-details" id={detailsId}>
      {job.observation_error && <p className="job-observation-error">{job.observation_error}</p>}
      {job.actions.length > 0 && <div className="job-controls">{job.actions.map(action => <IconButton key={action} icon={controls[action].icon}
        label={t(controls[action].label)} title={t(action === 'pause' ? controls.pause.title : controls[action].label)} size="sm" quiet
        disabled={disabled || busy} danger={action === 'cancel'} onClick={() => action === 'cancel' ? setConfirm(true) : void act(action)} />)}</div>}
      <dl className="job-metadata">
        {[
          ['Job provider', job.provider], ['Provider ID', job.external_id ?? '—'], ['Job ID', job.id], ['Agent', job.agent_name], ['Sandbox', job.sandbox_name],
          ['Submitted', timestamp(job.created_at)], ['Last observed', timestamp(job.last_observed_at)], ['Source run', job.source_run_id],
        ].map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd>{value}</dd></div>)}
        {job.detail && <div className="job-metadata-detail"><dt>{t('Details')}</dt><dd>{job.detail}</dd></div>}
      </dl>
      {confirm && job.actions.includes('cancel') && <div className="job-cancel-confirm" role="group" aria-label={t('Cancel external job')}>
        <p>{t('Request cancellation of this external job? Its status changes only after provider confirmation.')}</p>
        <button className="secondary-button nodrag nopan" disabled={busy} onClick={() => setConfirm(false)}>{t('Keep running')}</button>
        <button className="secondary-button nodrag nopan" disabled={disabled || busy} onClick={() => void act('cancel')}>{t('Cancel external job')}</button>
      </div>}
    </div>}
  </article>;
}

/** Provider-neutral board: the host supplies observations and allowed controls. */
export function JobsBoard({ api }: { api?: JobsHost }) {
  useLocale();
  const [snapshot, setSnapshot] = useState<JobsSnapshot>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchId = useId();
  const searchInput = useRef<HTMLInputElement>(null);
  const searchToggle = useRef<HTMLButtonElement>(null);
  const [provider, setProvider] = useState('');
  const refresh = useRef<(afterPending?: boolean) => Promise<void>>(async () => {});
  const generation = useRef(0);
  useEffect(() => { if (searchOpen) searchInput.current?.focus(); }, [searchOpen]);
  function closeSearch() {
    setSearchOpen(false); setQuery(''); searchToggle.current?.focus();
  }
  useEffect(() => {
    const token = ++generation.current;
    let timer: ReturnType<typeof setTimeout>;
    let pending: Promise<void> | undefined;
    setSnapshot(undefined); setError('');
    const load = (afterPending = false): Promise<void> => {
      if (!api || generation.current !== token) return Promise.resolve();
      if (pending) return afterPending ? pending.then(() => load()) : pending;
      clearTimeout(timer); setBusy(true);
      pending = api.list().then(value => {
        if (generation.current === token) { setSnapshot(value); setError(''); }
      }).catch(cause => {
        if (generation.current === token) { setSnapshot(undefined); setError(String(cause instanceof Error ? cause.message : cause)); }
      }).finally(() => {
        pending = undefined;
        if (generation.current === token) { setBusy(false); timer = setTimeout(() => void load(), 5000); }
      });
      return pending;
    };
    refresh.current = load;
    void load();
    return () => { ++generation.current; clearTimeout(timer); };
  }, [api]);
  async function control(job: BackgroundJob, action: JobAction) {
    if (!api) return;
    const token = generation.current;
    try {
      await api.control(job, action);
      if (token === generation.current) await refresh.current(true);
    } catch (cause) {
      if (token === generation.current) {
        await refresh.current(true);
        if (token === generation.current) setError(String(cause instanceof Error ? cause.message : cause));
      }
    }
  }
  const jobs = snapshot?.jobs ?? [];
  const counts = { all: jobs.length, active: 0, attention: 0, finished: 0 };
  jobs.forEach(job => counts[jobGroup(job)]++);
  const providers = [...new Set(jobs.map(job => job.provider))].sort();
  const selectedProvider = providers.includes(provider) ? provider : '';
  const visible = jobs.filter(job => (filter === 'all' || jobGroup(job) === filter) && (!selectedProvider || job.provider === selectedProvider)
    && [job.request_key, job.external_id, job.id, job.provider, job.agent_name, job.sandbox_name].some(value => value?.toLowerCase().includes(query.trim().toLowerCase())));
  return <section className="jobs-board nowheel" aria-label={t('Jobs')}>
    <WorkspaceToolbar className="jobs-toolbar" label={t('Filter jobs')}>
      <select className="jobs-filter nodrag nopan" aria-label={t('Filter jobs')} value={filter} disabled={!jobs.length} onChange={event => setFilter(event.target.value)}>
        {([['all', 'All'], ['active', 'Active'], ['attention', 'Needs attention'], ['finished', 'Finished']] as const).map(([key, label]) =>
          <option key={key} value={key}>{t(label)} · {counts[key]}</option>)}
      </select>
      {providers.length > 1 && <select className="jobs-provider nodrag nopan" aria-label={t('Job provider')} title={selectedProvider || t('All providers')}
        value={selectedProvider} onChange={event => setProvider(event.target.value)}>
        <option value="">{t('All providers')}</option>{providers.map(name => <option key={name}>{name}</option>)}</select>}
      <div className="jobs-toolbar-actions">
        <IconButton icon={RefreshCw} label={t('Refresh jobs')} disabled={busy || !api} onClick={() => void refresh.current()} size="sm" quiet />
        {searchOpen && <input ref={searchInput} id={searchId} className="jobs-search-input nodrag nopan" aria-label={t('Search jobs')}
          placeholder={t('Search jobs')} value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => {
            event.stopPropagation();
            if (event.key === 'Escape') { event.preventDefault(); closeSearch(); }
          }} />}
        <IconButton ref={searchToggle} icon={searchOpen ? X : Search} label={t(searchOpen ? 'Close job search' : 'Search jobs')} size="sm" quiet
          aria-expanded={searchOpen} aria-controls={searchOpen ? searchId : undefined} disabled={!api}
          onClick={() => searchOpen ? closeSearch() : setSearchOpen(true)} />
      </div>
    </WorkspaceToolbar>
    {error && <p className="jobs-error" role="alert">{error}</p>}
    {!api ? <WorkspaceEmptyState title={t('Jobs are available in the editor workspace.')} />
      : !snapshot ? !error && <WorkspaceEmptyState title={t('Loading jobs…')} busy />
      : !snapshot.sources.length ? <WorkspaceEmptyState icon={<Server size={24} />} title={t('Connect an Agent')}
        description={t('Use Observe jobs to view its work, or Manage jobs to enable controls.')} />
      : !jobs.length ? <WorkspaceEmptyState title={t('No jobs in this session')} description={t('Background jobs from connected Agents appear here automatically.')} />
      : <>
        <div className="jobs-grid">{visible.map(job => <JobTile key={job.id} job={job} disabled={busy} onAction={action => control(job, action)} />)}</div>
        {!visible.length && <WorkspaceEmptyState title={t('No matching jobs')} />}
      </>}
  </section>;
}
