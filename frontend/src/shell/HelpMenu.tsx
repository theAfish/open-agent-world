import { useReactFlow } from '@xyflow/react';
import { Activity, BookOpen, CheckCircle2, CircleHelp, Compass, Download, ExternalLink, Info, RefreshCw, TriangleAlert, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { worldApi } from '../api/client';
import { t, useLocale } from '../i18n';
import { tutorial, useTutorialStore } from '../onboarding/controller';
import { useCardLibrary } from '../state/cardLibrary';
import { useWorldStore } from '../state/worldStore';
import { checkMessage, DOCS_URL, RELEASES_URL, repairGuide, type HelpCheck, type HelpDiagnostics } from './helpChecks';
import './helpMenu.css';

type Panel = 'diagnostics' | 'updates';

export function HelpMenu() {
  const { locale } = useLocale();
  const busy = useTutorialStore(state => state.busy);
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<Panel>();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const closeMenu = (restore = false) => { setOpen(false); if (restore) trigger.current?.focus(); };

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current!.getBoundingClientRect();
      const height = menu.current!.offsetHeight;
      const width = menu.current!.offsetWidth;
      setPosition({ left: Math.max(12, Math.min(anchor.left, innerWidth - width - 12)),
        top: Math.max(12, Math.min(anchor.top >= height + 20 ? anchor.top - height - 8 : anchor.bottom + 8, innerHeight - height - 12)) });
    };
    place();
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open, locale]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent | FocusEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !trigger.current?.contains(event.target)) closeMenu();
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside); };
  }, [open]);

  const menuKey = (event: KeyboardEvent) => {
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(true); return; }
    if (event.key === 'Tab') { closeMenu(true); return; }
    const items = [...menu.current!.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)')];
    const current = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
      : event.key === 'ArrowDown' ? (current + 1) % items.length : event.key === 'ArrowUp' ? (current + items.length - 1) % items.length : -1;
    if (next >= 0) { event.preventDefault(); items[next].focus(); }
  };
  const show = (value: Panel) => {
    closeMenu();
    if (useTutorialStore.getState().view === 'active') tutorial.pause();
    setPanel(value);
  };

  return <>
    <button ref={trigger} type="button" className="top-icon-button" data-help-trigger aria-label={t('Help')} title={t('Help')}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? 'oaw-help-menu' : undefined}
      onClick={() => setOpen(value => !value)} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true); }
        if (event.key === 'Escape') closeMenu(true);
      }}><CircleHelp size={16} /></button>
    {open && createPortal(<div ref={menu} id="oaw-help-menu" className="help-menu" role="menu" aria-label={t('Help')}
      style={position} onKeyDown={menuKey}>
      <button role="menuitem" disabled={busy} onClick={() => { closeMenu(true); void tutorial.replay(); }}><Compass size={17} /><span>{t('Tutorial')}</span></button>
      <a role="menuitem" href={`${DOCS_URL}${locale === 'zh-CN' ? 'README.zh-CN/' : ''}`} target="_blank" rel="noopener noreferrer" onClick={() => closeMenu(true)}><BookOpen size={17} /><span>{t('Documentation')}</span><ExternalLink size={13} /></a>
      <button role="menuitem" onClick={() => show('diagnostics')}><Activity size={17} /><span>{t('Status check')}</span></button>
      <button role="menuitem" onClick={() => show('updates')}><Download size={17} /><span>{t('Versions and updates')}</span></button>
    </div>, document.body)}
    {panel && <HelpDialog panel={panel} onClose={() => { setPanel(undefined); trigger.current?.focus(); }} />}
  </>;
}

function HelpDialog({ panel, onClose }: { panel: Panel; onClose: () => void }) {
  useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const element = dialog.current!; element.showModal(); return () => element.close(); }, []);
  // Leave the native modal top layer before restoring focus or opening Settings.
  const close = () => { dialog.current?.close(); onClose(); };
  return createPortal(<dialog ref={dialog} className="help-dialog" aria-labelledby="help-dialog-title"
    onCancel={event => { event.preventDefault(); close(); }} onKeyDown={event => event.stopPropagation()}
    onClick={event => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
    }}>
    <header><h2 id="help-dialog-title">{t(panel === 'diagnostics' ? 'Status check' : 'Versions and updates')}</h2>
      <button className="icon-button" aria-label={t('Close help')} onClick={close} autoFocus><X size={18} /></button></header>
    {panel === 'diagnostics' ? <StatusCheck onClose={close} /> : <div className="help-update-content">
      <Download size={30} aria-hidden="true" />
      <h3>{t('Update OAW')}</h3>
      <p>{t('Automatic installation is not available in this version. Download the latest official installer, close OAW, then install the update.')}</p>
      <a className="primary-button" href={RELEASES_URL} target="_blank" rel="noopener noreferrer">{t('Open official releases')} <ExternalLink size={14} /></a>
      <p className="help-muted">{t('Running from source or using a hosted workspace? Update the source installation or ask your administrator to update the server.')}</p>
      <a href={`${DOCS_URL}install/`} target="_blank" rel="noopener noreferrer">{t('Installation guide')}</a>
    </div>}
  </dialog>, document.body);
}

function StatusCheck({ onClose }: { onClose: () => void }) {
  const { locale } = useLocale();
  const tutorialBusy = useTutorialStore(state => state.busy);
  const socket = useWorldStore(state => state.socketState);
  const { setCenter } = useReactFlow();
  const [report, setReport] = useState<HelpDiagnostics>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(false); setReport(undefined);
    const timer = window.setTimeout(() => controller.abort(), 15_000);
    void worldApi.getDiagnostics(controller.signal).then(value => { if (active) setReport(value); })
      .catch(() => { if (active) setError(true); })
      .finally(() => { window.clearTimeout(timer); if (active) setLoading(false); });
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [attempt]);

  const checks: HelpCheck[] = report ? [{ id: 'socket', status: socket === 'live' ? 'ok' : 'warning', code: socket === 'live' ? 'socket_live' : 'socket_offline' }, ...report.checks] : [];
  const warnings = checks.filter(check => check.status === 'warning').length;
  const info = checks.filter(check => check.status === 'info').length;
  const visible = checks.filter(check => showAll || check.status !== 'ok');
  const leave = () => {
    if (useTutorialStore.getState().view === 'active') tutorial.pause();
    onClose();
  };
  const openSettings = () => { leave(); useWorldStore.setState({ settingsOpen: true }); };
  const locate = (check: HelpCheck) => {
    if (check.x == null || check.y == null || !check.focus_id) return;
    leave();
    useWorldStore.getState().selectCards([check.focus_id]);
    void setCenter(check.x + 48, check.y + 48, { zoom: .9,
      duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 300 });
  };

  return <div className="help-diagnostics">
    <p className="help-muted">{t('Checks saved configuration and current runtime status across the canvas. Does not run Agents, execute commands or change your data.')}</p>
    <div className="help-check-toolbar">
      <button className="secondary-button" disabled={loading} onClick={() => setAttempt(value => value + 1)}><RefreshCw size={14} className={loading ? 'is-spinning' : ''} />{t(loading ? 'Checking…' : 'Check again')}</button>
      {report && <label><input type="checkbox" checked={showAll} onChange={event => setShowAll(event.target.checked)} />{t('Show passed checks')}</label>}
    </div>
    <div role="status" aria-live="polite" className="help-check-summary">
      {loading ? t('Checking components…') : report ? <>
        <strong>{warnings ? t('{count} items need attention', { count: warnings }) : t('No issues found in the checks performed')}</strong>
        <span>{t('{cards} cards checked · {count} notes or unverified checks', { cards: report.card_count, count: info })}</span>
        <small>{t('Checked at {time}', { time: new Date(report.checked_at).toLocaleTimeString(locale) })}</small>
      </> : null}
    </div>
    {error && <div className="help-check-error" role="alert">
      <strong>{t('The status check could not finish')}</strong>
      <p>{t('Make sure OAW is running, then check again. For the desktop app, close and reopen it after saving your work. For a hosted workspace, contact the administrator.')}</p>
      <a href={`${DOCS_URL}user-guide/troubleshooting/`} target="_blank" rel="noopener noreferrer">{t('Troubleshooting guide')}</a>
    </div>}
    <div className="help-check-list">
      {visible.map(check => <article key={check.id} className={`help-check help-check--${check.status}`}>
        <div className="help-check-heading">
          {check.status === 'warning' ? <TriangleAlert size={17} /> : check.status === 'info' ? <Info size={17} /> : <CheckCircle2 size={17} />}
          <strong>{check.name ?? t(check.id === 'socket' ? 'Live updates' : check.id === 'backend' ? 'Backend connection' : check.id === 'connections' ? 'Canvas connections' : 'Pack environments')}</strong>
          <span>{t(check.status === 'warning' ? 'Needs attention' : check.status === 'info' ? 'Note / unverified' : 'Check passed')}</span>
        </div>
        <p>{checkMessage(check.code)}</p>
        {check.status !== 'ok' && <div className="help-check-actions">
          {check.focus_id && <button className="secondary-button" disabled={tutorialBusy} onClick={() => locate(check)}>{t('Locate on canvas')}</button>}
          {(check.code === 'model_configuration' || check.code === 'legacy_model') && <button className="secondary-button" disabled={tutorialBusy} onClick={openSettings}>{t('Open settings')}</button>}
          {check.code === 'plugin_unavailable' && <button className="secondary-button" disabled={tutorialBusy} onClick={() => { leave(); useCardLibrary.getState().show(); }}>{t('Open Library')}</button>}
          <a href={repairGuide(check.code)} target="_blank" rel="noopener noreferrer">{t('Repair guide')} <ExternalLink size={12} /></a>
        </div>}
      </article>)}
    </div>
    {report && <p className="help-muted">{t('A passed configuration check does not guarantee an external service will respond. Recheck after making changes.')}</p>}
  </div>;
}
