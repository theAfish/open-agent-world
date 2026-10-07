import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronDown, Plus, Trash2 } from 'lucide-react';
import { worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import type { PluginCatalog, WorldCard } from '../types/world';
import type { StateMachineEventDescriptor } from './apiTypes';
import { EventPicker } from './EventPicker';
import { eventLabel } from './eventLabels';
import { parseExpression, printExpression, tryParseExpression, validateProgram } from './expressions';
import type { StateMachine, TriggerProgram, TriggerSignal } from './model';
import './triggerProgram.css';

type Template = 'single' | 'all' | 'any' | 'count' | 'formula';
const templates: [Template, string][] = [['single', 'Once'], ['count', 'After N times'], ['all', 'All events'], ['any', 'Any event']];
const positiveInteger = (value: string) => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 1e12;
const validWindow = (value: string) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 1 && Number(value) <= 86400;
const nextSignalId = (signals: TriggerSignal[]) => Array.from({ length: 26 }, (_, index) => String.fromCharCode(65 + index)).find(id => !signals.some(signal => signal.id === id))!;
const counted = (signals: TriggerSignal[], separator: string) => signals.map(signal => `count(${signal.id}) >= 1`).join(separator);

function describeTemplate(program: TriggerProgram): { mode: Template; countSignal: string; threshold: string } {
  const first = program.signals[0]?.id ?? 'A';
  const printed = printExpression(program.expression).replace(/\s+/g, '');
  if (printed === `event(${first})`) return { mode: 'single', countSignal: first, threshold: '3' };
  const count = /^count\(([A-Za-z][A-Za-z0-9_]*)\)>=(\d+)$/.exec(printed);
  if (count) return { mode: 'count', countSignal: count[1], threshold: count[2] };
  for (const [mode, separator] of [['all', ' && '], ['any', ' || ']] as const) {
    if (program.signals.length > 1 && printExpression(parseExpression(counted(program.signals, separator))).replace(/\s+/g, '') === printed) return { mode, countSignal: first, threshold: '3' };
  }
  return { mode: 'formula', countSignal: first, threshold: '3' };
}

export interface TriggerProgramEditorProps {
  program: TriggerProgram;
  machine: StateMachine;
  cards: WorldCard[];
  catalog: PluginCatalog;
  hidePrimaryPicker?: boolean;
  onChange: (program: TriggerProgram) => void;
  onValidityChange?: (valid: boolean) => void;
}

/** Action choices are the main flow. The same expression remains editable behind Advanced conditions. */
export function TriggerProgramEditor({ program, machine, cards, catalog, onChange, onValidityChange, hidePrimaryPicker = false }: TriggerProgramEditorProps) {
  useLocale();
  const initial = describeTemplate(program);
  const [mode, setMode] = useState<Template>(initial.mode);
  const [advanced, setAdvanced] = useState(false);
  const [countSignal, setCountSignal] = useState(initial.countSignal);
  const [threshold, setThreshold] = useState(initial.threshold);
  const [formula, setFormula] = useState(() => printExpression(program.expression));
  const [windowText, setWindowText] = useState(String(program.window_seconds ?? ''));
  const [windowEnabled, setWindowEnabled] = useState(program.window_seconds != null);
  const [events, setEvents] = useState<StateMachineEventDescriptor[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsError, setEventsError] = useState(false);
  const [retry, setRetry] = useState(0);
  const formulaId = useId();
  const formulaErrorId = `${formulaId}-error`;
  const expressionKey = JSON.stringify(program.expression);
  const previousExpression = useRef(expressionKey);
  const emittedExpression = useRef<string>();
  const externalExpressionChange = previousExpression.current !== expressionKey && emittedExpression.current !== expressionKey;
  const validityCallback = useRef(onValidityChange);
  validityCallback.current = onValidityChange;

  useEffect(() => {
    let active = true;
    setEventsLoading(true);
    setEventsError(false);
    void worldApi.getStateMachineEvents().then(response => { if (active) setEvents(response.events); })
      .catch(() => { if (active) setEventsError(true); })
      .finally(() => { if (active) setEventsLoading(false); });
    return () => { active = false; };
  }, [retry]);

  useEffect(() => {
    if (previousExpression.current === expressionKey) return;
    previousExpression.current = expressionKey;
    if (emittedExpression.current === expressionKey) return;
    const description = describeTemplate(program);
    setMode(description.mode);
    setCountSignal(description.countSignal);
    setThreshold(description.threshold);
    setFormula(printExpression(program.expression));
  }, [expressionKey, program]);
  useEffect(() => { setWindowText(String(program.window_seconds ?? '')); setWindowEnabled(program.window_seconds != null); }, [program.window_seconds]);

  const parsed = useMemo(() => tryParseExpression(formula), [formula]);
  const validation = useMemo(() => parsed.expression ? validateProgram({ ...program, expression: parsed.expression }, machine) : [], [parsed, program, machine]);
  const invalidThreshold = mode === 'count' && !positiveInteger(threshold);
  const invalidWindow = windowEnabled && !validWindow(windowText);
  const valid = !!parsed.expression && !validation.length && !invalidThreshold && !invalidWindow;
  useEffect(() => { validityCallback.current?.(valid); }, [valid]);
  useEffect(() => () => { validityCallback.current?.(true); }, []);
  useEffect(() => {
    if (externalExpressionChange || !valid || !parsed.expression || JSON.stringify(parsed.expression) === expressionKey) return;
    emittedExpression.current = JSON.stringify(parsed.expression);
    onChange({ ...program, expression: parsed.expression });
  }, [valid, parsed, expressionKey, externalExpressionChange, program, onChange]);

  const commit = (next: TriggerProgram, preserveFormula = false) => {
    emittedExpression.current = JSON.stringify(next.expression);
    if (!preserveFormula) setFormula(printExpression(next.expression));
    onChange(next);
  };
  const templateExpression = (template: Template, signals: TriggerSignal[], selected = countSignal, thresholdText = threshold) => {
    const first = signals[0]?.id ?? 'A';
    if (template === 'single') return parseExpression(`event(${first})`);
    if (template === 'all' || template === 'any') return parseExpression(counted(signals, template === 'all' ? ' && ' : ' || '));
    if (template === 'count') return parseExpression(`count(${signals.some(signal => signal.id === selected) ? selected : first}) >= ${positiveInteger(thresholdText) ? Number(thresholdText) : 3}`);
    return program.expression;
  };
  const withExtraSignal = (signals: TriggerSignal[]) => {
    const id = nextSignalId(signals);
    const first = signals[0].match;
    const alternate = events.find(event => !signals.some(signal => signal.match.event === event.key));
    const key = alternate?.key ?? 'unconfigured';
    return [...signals, { id, label: eventLabel(key, events), match: { entity_id: first.entity_id, event: key } }];
  };
  const chooseTemplate = (template: Template) => {
    setMode(template);
    if (template === 'formula') return;
    if (!positiveInteger(threshold)) setThreshold('3');
    const signals = (template === 'all' || template === 'any') && program.signals.length < 2 ? withExtraSignal(program.signals) : program.signals;
    commit({ ...program, signals, expression: templateExpression(template, signals) });
  };
  const updateSignal = (id: string, patch: Partial<TriggerSignal>) => {
    commit({ ...program, signals: program.signals.map(signal => signal.id === id ? { ...signal, ...patch } : signal) }, true);
  };
  const addSignal = () => {
    const signals = withExtraSignal(program.signals);
    const template = mode === 'single' ? 'all' : mode;
    setMode(template);
    commit({ ...program, signals, expression: templateExpression(template, signals) }, template === 'formula');
  };
  const removeSignal = (id: string) => {
    const signals = program.signals.filter(signal => signal.id !== id);
    const selected = countSignal === id ? signals[0].id : countSignal;
    setCountSignal(selected);
    commit({ ...program, signals, expression: templateExpression(mode, signals, selected) }, mode === 'formula');
  };
  const signalReferenced = (id: string) => new RegExp(`\\b(?:event|count)\\(\\s*${id}\\s*\\)`).test(formula);
  const formulaError = parsed.error ? t('Formula error at line {line}, column {column}: {message}', { line: parsed.line, column: parsed.column, message: parsed.error }) : validation.join(' / ');
  const visibleSignals = advanced || mode === 'all' || mode === 'any' || mode === 'formula' ? program.signals : [mode === 'count' ? program.signals.find(signal => signal.id === countSignal) ?? program.signals[0] : program.signals[0]];

  return <section className="sm-trigger-program" aria-label={t('Trigger conditions')}>
    {eventsLoading && <p className="sm-help" role="status">{t('Loading events...')}</p>}
    {eventsError && <div className="sm-error" role="alert">{t('Events could not be loaded.')}<button type="button" className="sm-text-button" onClick={() => setRetry(current => current + 1)}>{t('Retry')}</button></div>}
    <div className="sm-trigger-signals">{visibleSignals.map((signal, index) => {
      const cannotRemove = program.signals.length <= 1 || (mode === 'formula' && signalReferenced(signal.id));
      return <div className={`sm-trigger-signal ${visibleSignals.length > 1 ? 'multiple' : ''}`} key={signal.id}>
        {visibleSignals.length > 1 && <div className="sm-trigger-signal-header"><span>{t('Event {number}', { number: index + 1 })}</span><button type="button" className="icon-button" disabled={cannotRemove} title={mode === 'formula' && signalReferenced(signal.id) ? t('Remove this signal from the formula before deleting it.') : undefined} aria-label={t('Remove event {number}', { number: index + 1 })} onClick={() => removeSignal(signal.id)}><Trash2 size={13} /></button></div>}
        {(!hidePrimaryPicker || index > 0 || advanced) && <EventPicker value={signal.match} machine={machine} cards={cards} catalog={catalog} events={events} advanced={advanced} onChange={match => updateSignal(signal.id, { match, label: eventLabel(match.event, events) })} />}
      </div>;
    })}</div>
    {(mode === 'all' || mode === 'any' || advanced) && <button type="button" className="sm-text-button" disabled={program.signals.length >= 16} onClick={addSignal}><Plus size={14} />{t('Add another event')}</button>}
    <div className={`sm-trigger-behavior ${mode === 'count' ? 'with-count' : ''}`}>
      <label>{t('Trigger behavior')}<select value={mode} onChange={event => chooseTemplate(event.target.value as Template)}>{templates.map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}{mode === 'formula' && <option value="formula">{t('Custom conditions')}</option>}</select></label>
      {mode === 'count' && <label>{t('Times')}<input type="number" min={1} max={1e12} step={1} inputMode="numeric" aria-invalid={invalidThreshold} value={threshold} onChange={event => {
        const value = event.target.value;
        setThreshold(value);
        if (positiveInteger(value)) commit({ ...program, expression: templateExpression('count', program.signals, countSignal, value) });
      }} /></label>}
    </div>
    {mode === 'all' && <p className="sm-help">{t('These events can happen in any order.')}</p>}
    {mode === 'formula' && !advanced && <p className="sm-help">{t('Uses custom conditions. Open Advanced conditions to edit.')}</p>}
    {!advanced && (windowEnabled || program.reset === 'manual') && <p className="sm-help">{t('Additional counting settings are saved in Advanced conditions.')}</p>}
    {invalidThreshold && <p role="alert" className="sm-error">{t('Enter a whole-number threshold from 1 to 1000000000000.')}</p>}
    <button type="button" className="sm-trigger-advanced-toggle" aria-expanded={advanced} aria-controls={`${formulaId}-advanced`} disabled={advanced && !valid} onClick={() => setAdvanced(current => !current)}>{t('Advanced conditions')}<ChevronDown size={14} /></button>
    {advanced && <div className="sm-trigger-advanced" id={`${formulaId}-advanced`}>
      <div className="sm-trigger-aliases">{program.signals.map(signal => <label key={signal.id}><code>{signal.id}</code><input aria-label={t('Signal name {id}', { id: signal.id })} maxLength={200} value={signal.label} onChange={event => updateSignal(signal.id, { label: event.target.value })} /></label>)}</div>
      {mode === 'count' && program.signals.length > 1 && <label>{t('Count event')}<select value={countSignal} onChange={event => { setCountSignal(event.target.value); commit({ ...program, expression: templateExpression('count', program.signals, event.target.value) }); }}>{program.signals.map(signal => <option key={signal.id} value={signal.id}>{signal.id} · {signal.label}</option>)}</select></label>}
      <label htmlFor={formulaId}>{t('Trigger formula')}</label>
      <textarea id={formulaId} className="sm-trigger-formula" value={formula} spellCheck={false} rows={4} maxLength={8192} aria-invalid={!!formulaError} aria-describedby={formulaError ? formulaErrorId : undefined} onChange={event => { setMode('formula'); setFormula(event.target.value); }} />
      {formulaError && <p id={formulaErrorId} role="alert" className="sm-error">{formulaError}</p>}
      <details className="sm-trigger-reference"><summary>{t('Formula help')}</summary><p className="sm-help">{t('count(A) counts occurrences; event(A) checks this incoming event.')}</p><code>count(A) &gt;= 1 &amp;&amp; count(B) &gt;= 3</code><p className="sm-help">{t('Use + - * / % for numbers, == != < <= > >= to compare, and && || ! for logic. The final result must be true or false.')}</p>{machine.entities.map(entity => <div key={entity.id}><strong>{entity.label}</strong>{entity.states.map(state => <button type="button" key={state.id} className="sm-trigger-state-token" title={t('Insert state check')} onClick={() => {
        const token = `state(${JSON.stringify(entity.id)}, ${JSON.stringify(state.id)})`;
        setMode('formula');
        setFormula(formula.trim() ? `(${formula}) && ${token}` : token);
      }}>{state.label}</button>)}</div>)}</details>
      <div className="sm-trigger-counter-settings"><label>{t('Counting window')}<select value={windowEnabled ? 'limited' : 'unlimited'} onChange={event => {
        const limited = event.target.value === 'limited';
        setWindowEnabled(limited);
        setWindowText(limited ? '60' : '');
        commit({ ...program, window_seconds: limited ? 60 : null }, true);
      }}><option value="unlimited">{t('Until reset')}</option><option value="limited">{t('Within a time window')}</option></select></label>
        {windowEnabled && <label>{t('Window seconds')}<input type="number" min={1} max={86400} step="any" inputMode="decimal" aria-invalid={invalidWindow} value={windowText} onChange={event => {
          const value = event.target.value;
          setWindowText(value);
          if (validWindow(value)) commit({ ...program, window_seconds: Number(value) }, true);
        }} /></label>}
        {invalidWindow && <p role="alert" className="sm-error">{t('Enter a window from 1 to 86400 seconds.')}</p>}
        <label>{t('After matching')}<select value={program.reset} onChange={event => commit({ ...program, reset: event.target.value as TriggerProgram['reset'] }, true)}><option value="on_match">{t('Start counting again')}</option><option value="manual">{t('Keep counts until reset')}</option></select></label>
      </div>
    </div>}
    {!advanced && formulaError && <p role="alert" className="sm-error">{t('Open Advanced conditions to repair this condition.')}</p>}
  </section>;
}
