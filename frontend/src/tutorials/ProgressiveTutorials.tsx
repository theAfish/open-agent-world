import { BookOpen, ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t, useLocale } from '../i18n';
import { useTutorialStore } from '../onboarding/controller';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { tutorialText } from './catalog';
import { observeTutorialEncounters } from './observe';
import { currentStep, useProgressiveTutorials } from './store';
import './tutorials.css';

/** Links are explicit external navigation; images/HTML cannot issue hidden requests. */
export function TutorialMarkdown({ children }: { children: string }) {
  return <ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => href && /^https?:\/\//i.test(href)
      ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
    img: ({ alt }) => <span>{alt}</span>,
  }}>{children}</ReactMarkdown>;
}

export function ProgressiveTutorials() {
  useEffect(observeTutorialEncounters, []);
  const state = useProgressiveTutorials();
  const onboarding = useTutorialStore(state => state.view === 'active' || state.view === 'welcome' || !!state.quickStart);
  const dragging = useNodeSurfaceStore(state => state.dragging || !!state.connectingNodeId);
  const [modal, setModal] = useState<Element | null>(null);
  useEffect(() => {
    const update = () => setModal([...document.querySelectorAll('dialog[open]')].filter(element => !element.classList.contains('tutorial-library')).at(-1) ?? null);
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open'] });
    return () => observer.disconnect();
  }, []);
  const available = !onboarding && !dragging && (!modal || modal.classList.contains('legion-workspace'));
  return <>
    {state.libraryOpen && <TutorialLibrary />}
    {state.current && !state.libraryOpen && available && createPortal(<TutorialReader key={state.current} />, modal ?? document.body)}
  </>;
}

function TutorialReader() {
  const { locale } = useLocale();
  const state = useProgressiveTutorials();
  const entry = state.entries.find(item => item.key === state.current);
  const heading = useRef<HTMLHeadingElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (state.view === 'hint') return;
    const previous = document.activeElement as HTMLElement | null;
    heading.current?.focus({ preventScroll: true });
    return () => { if (panel.current?.contains(document.activeElement)) previous?.focus(); };
  }, [state.view]);
  if (!entry) return null;
  const definition = entry.definition, steps = definition.steps ?? [];
  const index = currentStep(entry, state.progress[entry.key]), step = steps[index];
  const text = (value: Parameters<typeof tutorialText>[0]) => tutorialText(value, locale);
  return <aside ref={panel} className={`progressive-tutorial is-${state.view}`} aria-labelledby="progressive-tutorial-title"
    onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') { event.preventDefault(); state.dismiss(); } }}>
    <header><BookOpen size={18} aria-hidden="true" /><span>{entry.ownerName}</span>
      <button className="icon-button" aria-label={t('Dismiss this tutorial')} title={t('Dismiss this tutorial')} onClick={state.dismiss}><X size={16} /></button></header>
    <div className="progressive-tutorial-content">
      <h2 ref={heading} id="progressive-tutorial-title" tabIndex={-1} aria-live="polite">{text(definition.title)}</h2>
      {state.view === 'hint' ? <p>{text(definition.summary)}</p> : state.view === 'document'
        ? <TutorialMarkdown>{text(definition.document ?? definition.summary)}</TutorialMarkdown>
        : step && <><p className="tutorial-step-count">{t('Step {current} of {total}', { current: index + 1, total: steps.length })}</p>
          <h3>{text(step.title)}</h3><TutorialMarkdown>{text(step.body)}</TutorialMarkdown></>}
    </div>
    <footer>
      {state.view === 'hint' && <button className="primary-button" onClick={() => state.open(entry.key)}>{t('View tutorial')}<ChevronRight size={15} /></button>}
      {state.view === 'steps' && <>
        <button className="secondary-button" disabled={index === 0} onClick={() => state.move(-1)}><ChevronLeft size={15} />{t('Previous')}</button>
        <button className="primary-button" onClick={() => index === steps.length - 1 ? state.complete() : state.move(1)}>{t(index === steps.length - 1 ? 'Finish tutorial' : 'Continue')}<ChevronRight size={15} /></button>
      </>}
      {state.view === 'document' && !!steps.length && <button className="secondary-button" onClick={() => state.open(entry.key)}>{t('View tutorial')}</button>}
      {state.view !== 'document' && definition.document && <button className="secondary-button" onClick={() => state.open(entry.key, true)}>{t('Read documentation')}</button>}
      {state.view === 'document' && !steps.length && <button className="primary-button" onClick={state.complete}>{t('Done')}</button>}
      <button className="tutorial-text-button" onClick={state.showLibrary}>{t('Card tutorials & docs')}</button>
    </footer>
  </aside>;
}

function TutorialLibrary() {
  const { locale } = useLocale();
  const state = useProgressiveTutorials();
  const [query, setQuery] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null, element = dialog.current!;
    element.showModal();
    return () => { element.close(); previous?.focus(); };
  }, []);
  const entries = state.entries.filter(entry => `${entry.ownerName} ${tutorialText(entry.definition.title, locale)} ${tutorialText(entry.definition.summary, locale)}`
    .toLocaleLowerCase(locale).includes(query.toLocaleLowerCase(locale)));
  return createPortal(<dialog ref={dialog} className="help-dialog tutorial-library" aria-labelledby="tutorial-library-title"
    onCancel={event => { event.preventDefault(); state.closeLibrary(); }} onKeyDown={event => event.stopPropagation()}>
    <header><h2 id="tutorial-library-title">{t('Card tutorials & docs')}</h2><button className="icon-button" aria-label={t('Close help')} onClick={state.closeLibrary}><X size={18} /></button></header>
    <div className="tutorial-library-content">
      <label className="tutorial-preference"><input type="checkbox" checked={state.enabled} onChange={event => state.setEnabled(event.target.checked)} />{t('Show tips when I encounter new cards')}</label>
      <p>{t('Dismissed tutorials stay here. You can resume reading at any time.')}</p>
      <input type="search" aria-label={t('Search card tutorials')} placeholder={t('Search card tutorials')} value={query} onChange={event => setQuery(event.target.value)} autoFocus />
      <div className="tutorial-library-list">{entries.map(entry => <article key={entry.key}>
        <small>{entry.ownerName}{state.progress[entry.key]?.status === 'completed' && state.progress[entry.key].revision === (entry.definition.revision ?? 1) ? ` · ${t('Completed')}` : ''}</small>
        <h3>{tutorialText(entry.definition.title, locale)}</h3><p>{tutorialText(entry.definition.summary, locale)}</p>
        <div>{!!entry.definition.steps?.length && <button className="secondary-button" onClick={() => state.open(entry.key)}>{t('View tutorial')}</button>}
          {entry.definition.document && <button className="secondary-button" onClick={() => state.open(entry.key, true)}>{t('Read documentation')}</button>}</div>
      </article>)}</div>
      {!entries.length && <p role="status">{t('No card tutorials found.')}</p>}
    </div>
  </dialog>, document.body);
}
