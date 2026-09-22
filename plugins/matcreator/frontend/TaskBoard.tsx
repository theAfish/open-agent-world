import { useCallback, useEffect, useRef, useState } from 'react';
import { t, useLocale, type PluginViewProps } from '@oaw/plugin-api';
import './tasks.css';

type Status = 'pending' | 'running' | 'review' | 'blocked' | 'done';
type Task = { id: string; title: string; description: string; acceptance?: string; depends_on: string[]; status: Status; result: string; outputs: string[] };
type Plan = { id: string; title: string; goal: string; tasks: Task[] };
type Snapshot = { value: { plans: Plan[] }; revision: number };
type Attempt = { item_id: string; instance_id: string | null; agent_id: string | null; run_id: string | null; status: string; error?: string; text?: string; output_directory: string; reconciliation_error?: string };
type Execution = { items: { id: string; metadata: { plan_id: string; task_id: string } }[]; attempts: Attempt[] };
type Collected = { document: Snapshot; execution: Execution };
const columns: { id: Status; label: string }[] = [
  { id: 'pending', label: 'To do' }, { id: 'running', label: 'In progress' },
  { id: 'review', label: 'Awaiting review' },
  { id: 'blocked', label: 'Blocked' }, { id: 'done', label: 'Done' },
];
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
function StatusIcon({ status, size = 15 }: { status: Status; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {status === 'done' ? <path d="m5 12 4 4L19 6" /> : status === 'running' ? <path d="m8 5 11 7-11 7Z" /> : <><circle cx="12" cy="12" r="8" />{status === 'blocked' && <path d="M12 8v5m0 3h.01" />}</>}
  </svg>;
}
// Wide glyphs need more room before sharing a row with another task.
const titleWidth = (title: string) => [...title].reduce((width, character) => width + (character.charCodeAt(0) > 255 ? 2 : 1), 0);

function useBoard(host: PluginViewProps['host']) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [execution, setExecution] = useState<Execution>();
  const [error, setError] = useState('');
  const alive = useRef(false);
  const accept = useCallback((next: Snapshot) => {
    if (alive.current) setSnapshot(previous => !previous || next.revision >= previous.revision ? next : previous);
  }, []);
  const refresh = useCallback(async () => {
    const collected = host.delegationAction ? await host.delegationAction('collect', {}) as unknown as Collected : undefined;
    const next = collected?.document ?? await host.readDocument() as Snapshot;
    if (alive.current && collected) setExecution(collected.execution);
    accept(next);
    return next;
  }, [host, accept]);
  useEffect(() => {
    alive.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { await refresh(); if (!stopped) setError(''); }
      catch (reason) { if (!stopped) setError(message(reason)); }
      if (!stopped) timer = setTimeout(() => void poll(), 3000);
    };
    void poll();
    return () => { stopped = true; alive.current = false; clearTimeout(timer); };
  }, [refresh]);
  return { snapshot, execution, error, refresh, accept };
}

export function TaskPreview({ host }: PluginViewProps) {
  useLocale();
  const { snapshot, error } = useBoard(host);
  const tasks = snapshot?.value.plans.flatMap(plan => plan.tasks) ?? [];
  return <div className="mc-task-preview"><small>{t('Research task board')}</small>
    <strong>{tasks.filter(task => task.status === 'done').length} / {tasks.length} {t('Done')}</strong>
    <p>{snapshot?.value.plans.at(-1)?.title ?? t('Plan, execute, verify and learn.')}</p>
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function TaskBoard({ host }: PluginViewProps) {
  useLocale();
  const { snapshot, execution, error: readError, refresh, accept } = useBoard(host);
  const [selected, setSelected] = useState('');
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [editor, setEditor] = useState<{ task: Task; planId: string; revision: number; fresh: boolean }>();
  const [taskId, setTaskId] = useState<string>();
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const surface = useRef<HTMLElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const listScroll = useRef(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<'activity' | 'board'>('activity');
  const details = useRef<HTMLDetailsElement>(null);
  const running = useRef(false);
  const plans = snapshot?.value.plans ?? [];
  const plan = plans.find(item => item.id === selected) ?? plans.at(-1);
  const done = plan?.tasks.filter(task => task.status === 'done').length ?? 0;
  const taskDetail = plan?.tasks.find(task => task.id === taskId);
  const itemId = execution?.items.find(item => item.metadata.plan_id === plan?.id && item.metadata.task_id === taskId)?.id;
  const attempts = execution?.attempts.filter(attempt => attempt.item_id === itemId) ?? [];
  const inTask = !!editor || taskId !== undefined;
  useEffect(() => {
    if (surface.current) surface.current.scrollTop = inTask ? 0 : listScroll.current;
    if (inTask && !editor) back.current?.focus();
  }, [taskId, !!editor]);

  function openTask(id: string) {
    setDescriptionExpanded(false);
    if (!inTask) listScroll.current = surface.current?.scrollTop ?? 0;
    if (plan) setSelected(plan.id);
    setTaskId(id); setError('');
  }
  function returnToTasks() { setTaskId(undefined); setEditor(undefined); setError(''); }

  async function mutate(action: string, arguments_: Record<string, unknown>, revision = snapshot?.revision) {
    if (running.current || revision === undefined) return false;
    running.current = true; setBusy(true); setError('');
    try {
      const next = await host.documentAction(action, arguments_, revision) as Snapshot;
      accept(next);
      if (action === 'create_plan') setSelected(next.value.plans.at(-1)!.id);
      return true;
    } catch (reason) { setError(message(reason)); return false; }
    finally { running.current = false; setBusy(false); }
  }

  function edit(task?: Task) {
    if (!plan || !snapshot) return;
    setSelected(plan.id);
    if (!inTask) listScroll.current = surface.current?.scrollTop ?? 0;
    setError(''); setCreating(false);
    setEditor({ planId: plan.id, revision: snapshot.revision, fresh: !task, task: task ?? {
      id: crypto.randomUUID(), title: '', description: '', depends_on: [], status: 'pending', result: '', outputs: [],
    } });
  }
  const patch = (value: Partial<Task>) => setEditor(current => current && ({ ...current, task: { ...current.task, ...value } }));
  const stale = editor && snapshot && editor.revision !== snapshot.revision;
  return <section ref={surface} className="mc-task-board nodrag nowheel" aria-label={t('Research task board')}>
    <header className="mc-task-header">
      <div className="mc-task-heading"><h3 title={plan?.title}>{plan?.title ?? t('Research plans')}</h3>
        {plan && <span className="mc-task-count" aria-label={t('Research progress')}>{done}/{plan.tasks.length}{plan.tasks.length > 0 && done === plan.tasks.length ? ' ✓' : ''}</span>}
        <details className="mc-task-menu" ref={details} onKeyDown={event => {
          if (event.key === 'Escape') { event.stopPropagation(); event.preventDefault(); event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus(); }
        }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}><summary aria-label={t('Plan details and actions')} title={t('Plan details and actions')}>···</summary>
          <div className="mc-task-menu-content">
      <label>{t('Research plans')}<select aria-label={t('Research plans')} value={plan?.id ?? ''} disabled={busy || !!editor}
        onChange={event => { setSelected(event.target.value); setTaskId(undefined); setView('activity'); setCreating(false); setError(''); if (details.current) details.current.open = false; }}>
        {!plans.length && <option value="">{t('No research plans yet')}</option>}
        {plans.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}
      </select></label>
      {plan && <div className="mc-task-plan-details"><p>{plan.goal}</p><small>{t('Plan ID')}: {plan.id}</small></div>}
      <button disabled={!snapshot || busy || !!editor} onClick={() => { setCreating(true); setTaskId(undefined); setError(''); if (details.current) details.current.open = false; }}>{t('New plan')}</button>
      <button disabled={!snapshot || busy} onClick={() => void refresh().catch(reason => setError(message(reason)))}>{t('Refresh')}</button>
      <p className="mc-task-hint">{t('Task status records progress. Use the conversation or Sandbox controls to stop execution.')}</p>
          </div>
        </details>
      </div>
      {!inTask && plan?.goal && <p className="mc-task-subtitle" title={plan.goal}>{plan.goal}</p>}
      {!inTask && plan && <progress aria-label={t('Research progress')} value={done} max={plan.tasks.length || 1} />}
    </header>
    {(error || readError) && <p className="mc-task-error" role="alert">{error || readError}</p>}
    {!snapshot && !readError && <p role="status">{t('Loading research tasks…')}</p>}
    {creating && <form className="mc-task-editor" onSubmit={async event => {
      event.preventDefault();
      if (await mutate('create_plan', { title: title.trim(), goal, tasks: [] })) {
        setCreating(false); setTitle(''); setGoal('');
      }
    }}>
      <h3>{t('New research plan')}</h3>
      <label>{t('Plan title')}<input autoFocus aria-label={t('Plan title')} required maxLength={180} value={title} onChange={event => setTitle(event.target.value)} /></label>
      <label>{t('Research goal')}<textarea aria-label={t('Research goal')} maxLength={8000} value={goal} onChange={event => setGoal(event.target.value)} /></label>
      <div className="mc-task-actions"><button disabled={busy || !title.trim()}>{t('Create plan')}</button><button type="button" disabled={busy} onClick={() => setCreating(false)}>{t('Cancel')}</button></div>
    </form>}
    {plan && !creating && <>
      {!inTask && <div className="mc-task-status-strip">
        <button className="mc-task-add" aria-label={t('Add task')} disabled={busy} onClick={() => edit()}>{t('+ Task')}</button>
        {columns.map(column => { const count = plan.tasks.filter(task => task.status === column.id).length;
          return <span key={column.id} className={`is-${column.id}${count ? ' has-tasks' : ''}`}>
            <StatusIcon status={column.id} size={13} />{t(column.id === 'running' ? 'Running' : column.label)} <b>{count}</b></span>;
        })}
        <button className="mc-task-board-toggle" aria-pressed={view === 'board'} onClick={() => setView(view === 'board' ? 'activity' : 'board')}>{t(view === 'board' ? 'Compact cards' : 'Open board')}<span aria-hidden="true">↗</span></button>
      </div>}
      {inTask && <nav className="mc-task-detail-nav"><button ref={back} disabled={busy} onClick={returnToTasks}>{t('‹ Tasks')}</button>
        {editor && taskId && <button disabled={busy} onClick={() => { setEditor(undefined); setError(''); }}>{t('‹ Detail')}</button>}
        {!editor && taskDetail && <button disabled={busy} onClick={() => edit(taskDetail)}>{t('Edit')}</button>}
      </nav>}
      {editor ? <form className="mc-task-editor" onSubmit={async event => {
        event.preventDefault();
        const { id, ...fields } = editor.task;
        const args = editor.fresh ? { plan_id: editor.planId, task: editor.task } : { plan_id: editor.planId, task_id: id, ...fields };
        if (await mutate(editor.fresh ? 'add_task' : 'update_task', args, editor.revision)) { setTaskId(id); setEditor(undefined); }
      }}>
        <h3>{t(editor.fresh ? 'Add task' : 'Edit task')}</h3>
        {stale && <div className="mc-task-conflict" role="status"><p>{t('The board changed while you were editing. Your draft is retained.')}</p>
          <button type="button" disabled={busy} onClick={() => {
            const current = plan.tasks.find(task => task.id === editor.task.id);
            if (current) setEditor({ ...editor, task: current, revision: snapshot!.revision });
            else if (editor.fresh) setEditor({ ...editor, revision: snapshot!.revision });
            else setError(t('This task was removed. Copy your draft before closing.'));
            if (current || editor.fresh) setError('');
          }}>{t(editor.fresh ? 'Use latest board revision' : 'Reload latest task')}</button></div>}
        <label>{t('Task title')}<input autoFocus aria-label={t('Task title')} required maxLength={180} value={editor.task.title} onChange={event => patch({ title: event.target.value })} /></label>
        <label>{t('Task details')}<textarea aria-label={t('Task details')} maxLength={8000} value={editor.task.description} onChange={event => patch({ description: event.target.value })} /></label>
        <label>{t('Acceptance criteria')}<textarea aria-label={t('Acceptance criteria')} maxLength={8000} value={editor.task.acceptance ?? ''} onChange={event => patch({ acceptance: event.target.value })} /></label>
        <label>{t('Task status')}<select aria-label={t('Task status')} value={editor.task.status} onChange={event => patch({ status: event.target.value as Status })}>
          {columns.map(column => <option key={column.id} value={column.id}>{t(column.label)}</option>)}
        </select></label>
        <p className="mc-task-hint">{t('This is recorded progress, not live execution state. Changing status does not start or stop a run.')}</p>
        {plan.tasks.some(task => task.id !== editor.task.id) && <fieldset><legend>{t('Prerequisite tasks')}</legend>
          {plan.tasks.filter(task => task.id !== editor.task.id).map(task => <label className="mc-task-dependency" key={task.id}>
            <input type="checkbox" checked={editor.task.depends_on.includes(task.id)} onChange={event => patch({ depends_on: event.target.checked
              ? [...editor.task.depends_on, task.id] : editor.task.depends_on.filter(id => id !== task.id) })} />{task.title}</label>)}
        </fieldset>}
        <label>{t('Result or blocker')}<textarea aria-label={t('Result or blocker')} required={editor.task.status === 'done'} maxLength={12000} value={editor.task.result} onChange={event => patch({ result: event.target.value })} /></label>
        <label>{t('Output paths (one per line)')}<textarea aria-label={t('Output paths (one per line)')} value={editor.task.outputs.join('\n')} onChange={event => patch({ outputs: event.target.value.split('\n') })} onBlur={() => patch({ outputs: editor.task.outputs.map(path => path.trim()).filter(Boolean) })} /></label>
        <div className="mc-task-actions"><button disabled={busy || !!stale || !editor.task.title.trim()}>{t('Save task')}</button>
          <button type="button" disabled={busy} onClick={() => { setEditor(undefined); setError(''); }}>{t('Cancel')}</button>
          {!editor.fresh && <button type="button" disabled={busy || !!stale} onClick={async () => {
            if (await mutate('remove_task', { plan_id: editor.planId, task_id: editor.task.id }, editor.revision)) returnToTasks();
          }}>{t('Remove task')}</button>}
        </div>
      </form> : taskId !== undefined ? <article className="mc-task-detail" aria-label={t('Task detail')}>
        {taskDetail ? <>
          <header className={`mc-task-detail-title is-${taskDetail.status}`}>
            <span className="mc-task-status-icon"><StatusIcon status={taskDetail.status} /></span>
            <div><h2>{taskDetail.title}</h2><span className="mc-task-detail-status">{t(columns.find(column => column.id === taskDetail.status)!.label)}</span></div>
          </header>
          {taskDetail.description && <section className="mc-task-detail-section"><h3>{t('Description')}</h3>
            <p>{!descriptionExpanded && taskDetail.description.length > 280 ? `${taskDetail.description.slice(0, 280)}…` : taskDetail.description}</p>
            {taskDetail.description.length > 280 && <button className="mc-task-description-toggle" aria-expanded={descriptionExpanded} onClick={() => setDescriptionExpanded(expanded => !expanded)}>{t(descriptionExpanded ? 'Collapse' : 'Full description')}</button>}
          </section>}
          {taskDetail.acceptance && <section className="mc-task-detail-section"><h3>{t('Acceptance criteria')}</h3><p>{taskDetail.acceptance}</p></section>}
          {taskDetail.status === 'review' && <p className="mc-task-hint" role="status">{t('Executor finished. Verify the outputs and record evidence before marking this task done.')}</p>}
          {!!attempts.length && <section className="mc-task-detail-section"><h3>{t('Executor attempts')}</h3>
            {attempts.map((attempt, index) => <div className="mc-task-attempt" key={attempt.instance_id ?? index}>
              <strong>{t('Attempt')} {index + 1} · {t(attempt.status)}</strong>
              <p>{t('Output directory')}: <code>{attempt.output_directory}</code></p>
              {(attempt.error || attempt.reconciliation_error) && <p role="alert">{attempt.error || attempt.reconciliation_error}</p>}
              {attempt.text && <details><summary>{t('Executor report')}</summary><p>{attempt.text}</p></details>}
              <details><summary>{t('Execution references')}</summary><p>Agent: {attempt.agent_id}<br />Run: {attempt.run_id}<br />Instance: {attempt.instance_id}</p></details>
              {attempt.instance_id && ['created', 'running', 'waiting'].includes(attempt.status) && <button disabled={busy} onClick={async () => {
                setBusy(true); setError('');
                try { await host.delegationAction('stop', { instance_id: attempt.instance_id }); await refresh(); }
                catch (reason) { setError(message(reason)); }
                finally { setBusy(false); }
              }}>{t('Stop task')}</button>}
            </div>)}
          </section>}
          <section className="mc-task-detail-section"><h3>{t('Prerequisite tasks')}</h3>
            {taskDetail.depends_on.length ? <ul className="mc-task-detail-dependencies">{taskDetail.depends_on.map(id => {
              const dependency = plan.tasks.find(task => task.id === id);
              return <li key={id} className={`is-${dependency?.status ?? 'blocked'}`}><span className="mc-task-status-icon"><StatusIcon status={dependency?.status ?? 'blocked'} /></span>
                <span>{dependency?.title ?? id}<small>{dependency ? t(columns.find(column => column.id === dependency.status)!.label) : t('Task no longer exists')}</small></span></li>;
            })}</ul> : <p className="mc-task-hint">{t('No prerequisites')}</p>}
          </section>
          {taskDetail.result && <section className="mc-task-detail-section"><h3>{t(taskDetail.status === 'blocked' ? 'Blocking reason' : taskDetail.status === 'running' ? 'Execution information' : 'Result')}</h3><p>{taskDetail.result}</p></section>}
          {!!taskDetail.outputs.length && <section className="mc-task-detail-section"><h3>{t('Outputs')}</h3><ul className="mc-task-detail-outputs">{taskDetail.outputs.map((output, index) => <li key={index}><code>{output}</code></li>)}</ul></section>}
          <dl className="mc-task-detail-references"><dt>{t('Task ID')}</dt><dd>{taskDetail.id}</dd></dl>
        </> : <p role="status">{t('Task no longer exists')}</p>}
      </article> : <div className={`mc-task-sections${view === 'board' ? ' wants-board' : ''}`}>
        {(['running', 'review', 'blocked', 'pending', 'done'] as Status[]).map(status => {
          const tasks = plan.tasks.filter(task => task.status === status);
          if (!tasks.length) return null;
          const label = { running: 'Running', review: 'Awaiting review', blocked: 'Needs attention', pending: 'Next', done: 'Completed' }[status];
          return <section className={`mc-task-section is-${status}`} aria-label={t(status === 'done' ? 'Done' : label)} key={status}>
            <h4><span className="mc-task-dot" />{t(label)}<span>{tasks.length}</span></h4>
            <div className={`mc-task-grid${tasks.some(task => titleWidth(task.title) > 42) ? ' has-long-titles' : ''}`}>
              {tasks.map(task => {
                const metadata = task.result || task.outputs.join(' · ') || (task.depends_on.length
                  ? `${t('After')}: ${task.depends_on.map(id => plan.tasks.find(item => item.id === id)?.title ?? id).join(', ')}` : '');
                return <button className="mc-task-item" aria-label={task.title} title={task.title} key={task.id} onClick={() => openTask(task.id)}>
                  <span className="mc-task-status-icon" title={t(columns.find(column => column.id === status)!.label)}><StatusIcon status={status} /></span>
                  <span className="mc-task-card-copy"><strong>{task.title}</strong>{metadata && <small className="mc-task-metadata">{metadata}</small>}</span>
                  <span className="mc-task-open" aria-hidden="true">↗</span>
                </button>;
              })}
            </div>
          </section>;
        })}
        {!plan.tasks.length && <p className="mc-task-list-empty">{t('No tasks yet. Add a task to get started.')}</p>}
      </div>}
    </>}
    {snapshot && !plan && !creating && <div className="mc-task-empty"><span>01 → 02 → 03</span><h3>{t('Plan, execute, verify and learn.')}</h3>
      <p>{t('Describe a materials research goal in the conversation, or create a plan here. Tasks keep dependencies, progress and results together.')}</p>
      <button onClick={() => setCreating(true)}>{t('New plan')}</button></div>}
  </section>;
}
