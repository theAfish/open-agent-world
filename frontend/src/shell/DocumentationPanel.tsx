import { ArrowLeft, ArrowRight, ExternalLink, Languages, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t, useLocale } from '../i18n';
import { documentation, documentationGroups, documentationLanguages, documentationLink, documentationText, documentIdentity, defaultDocumentationLocale, localizedPage, remarkDocumentationHeadings } from './documentation';
import { profileStorage } from '../state/profileStorage';
import './documentation.css';

export function DocumentationPanel({ initialPage = 'user-guide/index.md' }: { initialPage?: string }) {
  const { locale } = useLocale();
  const [languagePreference, setLanguagePreference] = useState(() => {
    try {
      const saved = profileStorage.getItem('oaw.documentation.locale');
      return documentationLanguages.some(item => item.locale === saved) ? saved! : '';
    } catch { return ''; }
  });
  const language = languagePreference || (documentationLanguages.some(item => item.locale === locale) ? locale : defaultDocumentationLocale);
  const [query, setQuery] = useState('');
  const [history, setHistory] = useState({ entries: [{ page: localizedPage(initialPage, language), hash: '' }], index: 0 });
  const article = useRef<HTMLElement>(null);
  const location = history.entries[history.index];
  const page = documentation[location.page] ? location.page : localizedPage('user-guide/index.md', language);
  const entry = documentation[page];
  const contentLanguage = documentIdentity(page).locale;
  const previousLocale = useRef(language);
  useEffect(() => { article.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    if (previousLocale.current === language) return;
    previousLocale.current = language;
    setQuery('');
    setHistory(current => ({ ...current, entries: current.entries.map(item => {
      const translated = localizedPage(item.page, language);
      return { page: translated, hash: translated === item.page ? item.hash : '' };
    }) }));
  }, [language]);
  useEffect(() => {
    const container = article.current;
    if (!container) return;
    const target = location.hash && [...container.querySelectorAll<HTMLElement>('[id]')].find(node => node.id === location.hash);
    container.scrollTop = target ? target.offsetTop - container.offsetTop : 0;
    if (target) { target.tabIndex = -1; target.focus({ preventScroll: true }); }
  }, [page, location.hash, history.index]);

  const navigate = (next: string, hash = '') => {
    if (page === next && location.hash === hash) return;
    setHistory(current => ({ entries: [...current.entries.slice(0, current.index + 1), { page: next, hash }], index: current.index + 1 }));
  };
  const search = query.trim().toLocaleLowerCase(language);
  const groups = documentationGroups(language).map(group => ({ ...group, pages: group.pages.filter(path =>
    !search || `${documentation[path].title}\n${documentation[path].markdown}`.toLocaleLowerCase(language).includes(search)) }));
  const components: Components = {
    a: ({ href, children }) => {
      const link = documentationLink(href ?? '', page, language);
      if (link.kind === 'page') return <a href={`#${link.hash || link.page}`} onClick={event => {
        event.preventDefault(); article.current?.focus({ preventScroll: true }); navigate(link.page, link.hash);
      }}>{children}</a>;
      if (link.kind === 'unavailable') return <span>{children}</span>;
      return <a href={link.url} target="_blank" rel="noopener noreferrer" title={link.kind === 'external' ? t('Opens online (internet required)') : undefined}>
        {children}{link.kind === 'external' && <ExternalLink size={12} className="docs-external-icon" aria-label={t('Opens online (internet required)')} />}
      </a>;
    },
    img: ({ src, alt }) => {
      const link = documentationLink(src ?? '', page);
      return link.kind === 'asset' ? <img src={link.url} alt={alt ?? ''} loading="lazy" /> : <span>{alt}</span>;
    },
    table: ({ children }) => <div className="docs-table-scroll"><table>{children}</table></div>,
  };

  return <div className="documentation-panel">
    <aside className="docs-sidebar">
      <label className="docs-search"><Search size={16} aria-hidden="true" />
        <input type="search" aria-label={t('Search documentation')} placeholder={t('Search documentation')} value={query} onChange={event => setQuery(event.target.value)} />
      </label>
      <nav aria-label={t('Documentation topics')}>
        {groups.map(group => group.pages.length > 0 && <section key={group.title}>
          <h3>{group.label}</h3>
          {group.pages.map(path => <button type="button" key={path} aria-current={localizedPage(page, language) === path ? 'page' : undefined}
            onClick={() => navigate(path)}>{documentation[path].title}</button>)}
        </section>)}
        {!groups.some(group => group.pages.length > 0) && <p role="status">{t('No documentation matches your search.')}</p>}
      </nav>
      <p className="docs-offline-note">{t('Included with this version · Available offline')}</p>
    </aside>
    <section className="docs-reader" aria-label={t('Documentation content')}>
      <div className="docs-toolbar">
        <button className="icon-button" aria-label={t('Previous topic')} disabled={history.index === 0}
          onClick={() => setHistory(current => ({ ...current, index: current.index - 1 }))}><ArrowLeft size={17} /></button>
        <button className="icon-button" aria-label={t('Next topic')} disabled={history.index === history.entries.length - 1}
          onClick={() => setHistory(current => ({ ...current, index: current.index + 1 }))}><ArrowRight size={17} /></button>
        <span className="docs-topic-title" aria-live="polite">{entry.title}</span>
        <label className="docs-language-picker">
          <Languages size={16} aria-hidden="true" />
          <select aria-label={documentationText(locale, 'language')} value={languagePreference} onChange={event => {
            const selected = event.target.value;
            setLanguagePreference(selected);
            try {
              if (selected) profileStorage.setItem('oaw.documentation.locale', selected);
              else profileStorage.removeItem('oaw.documentation.locale');
            } catch { /* Reading can continue without persistence. */ }
          }}>
            <option value="">{documentationText(locale, 'follow_app')}</option>
            {documentationLanguages.map(item => <option key={item.locale} value={item.locale}>{item.name}</option>)}
          </select>
        </label>
      </div>
      <article ref={article} className="docs-article" lang={contentLanguage} dir="auto" tabIndex={0} aria-label={entry.title}>
        {contentLanguage !== language && <p className="docs-language-note" lang={language}>{documentationText(language, 'fallback', {
          requested: documentationLanguages.find(item => item.locale === language)!.name,
          available: documentationLanguages.find(item => item.locale === contentLanguage)!.name,
        })}</p>}
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkDocumentationHeadings]} skipHtml components={components}>{entry.markdown}</ReactMarkdown>
      </article>
    </section>
  </div>;
}
