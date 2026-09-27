import { ArrowLeft, ArrowRight, ExternalLink, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t, useLocale } from '../i18n';
import { documentation, documentationGroups, documentationLink, localizedPage, remarkDocumentationHeadings } from './documentation';
import './documentation.css';

export function DocumentationPanel({ initialPage = 'user-guide/index.md' }: { initialPage?: string }) {
  const { locale } = useLocale();
  const [query, setQuery] = useState('');
  const [history, setHistory] = useState({ entries: [{ page: localizedPage(initialPage, locale), hash: '' }], index: 0 });
  const article = useRef<HTMLElement>(null);
  const location = history.entries[history.index];
  const page = documentation[location.page] ? location.page : localizedPage('user-guide/index.md', locale);
  const entry = documentation[page];
  const previousLocale = useRef(locale);
  useEffect(() => { article.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    if (previousLocale.current === locale) return;
    previousLocale.current = locale;
    setHistory(current => ({ ...current, entries: current.entries.map(item => ({ ...item, page: localizedPage(item.page, locale) })) }));
  }, [locale]);
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
  const search = query.trim().toLocaleLowerCase(locale);
  const groups = documentationGroups(locale).map(group => ({ ...group, pages: group.pages.filter(path =>
    !search || `${documentation[path].title}\n${documentation[path].markdown}`.toLocaleLowerCase(locale).includes(search)) }));
  const components: Components = {
    a: ({ href, children }) => {
      const link = documentationLink(href ?? '', page);
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
          <h3>{t(group.title)}</h3>
          {group.pages.map(path => <button type="button" key={path} aria-current={localizedPage(page, locale) === path ? 'page' : undefined}
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
        <span aria-live="polite">{entry.title}</span>
      </div>
      <article ref={article} className="docs-article" tabIndex={0} aria-label={entry.title}>
        {locale === 'zh-CN' && !page.endsWith('.zh-CN.md') && page !== 'install.md' && <p className="docs-language-note">{t('This topic is currently available in English.')}</p>}
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkDocumentationHeadings]} skipHtml components={components}>{entry.markdown}</ReactMarkdown>
      </article>
    </section>
  </div>;
}
