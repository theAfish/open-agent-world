import catalog from '../../../docs/documentation.json';
import { DOCS_URL } from './helpChecks';

// Eager raw imports put the manual in the application bundle, including on first
// use without internet. Keep repository-only notes out of the reader and search.
const sources = import.meta.glob<string>([
  '../../../docs/**/*.md', '!../../../docs/assets/**',
  '!../../../docs/internal/**',
], { query: '?raw', import: 'default', eager: true });
const assets = import.meta.glob<string>('../../../docs/assets/**/*.{png,gif,jpg,jpeg,svg,webp}',
  { query: '?url', import: 'default', eager: true });

export const documentation = Object.fromEntries(Object.entries(sources).map(([path, source]) => {
  let markdown = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '');
  // Only landing pages use MkDocs layout wrappers. Leave examples in other
  // pages untouched, including HTML/JSX inside fenced code blocks.
  if (path.startsWith('../../../docs/README.')) markdown = markdown
    .replace(/^<\/?div\b[^>]*>\s*$/gm, '')
    .replace(/(\]\([^\n)]+\))\{\s*\.[^}\n]+\}/g, '$1');
  return [path.replace('../../../docs/', ''), {
    title: markdown.match(/^#\s+(.+)$/m)?.[1].trim()
      ?? path.split('/').pop()!.replace(/\.md$/, '').replace(/-/g, ' ').replace(/^./, letter => letter.toUpperCase()), markdown,
  }];
}));

export const documentationAssets = Object.fromEntries(Object.entries(assets)
  .map(([path, url]) => [path.replace('../../../docs/', ''), url]));

export const documentationLanguages = catalog.plugins.i18n.languages.filter(language => language.build);
export const defaultDocumentationLocale = documentationLanguages.find(language => language.default)!.locale;

/** Topic identity is independent of its translation. The default source has no suffix. */
export function documentIdentity(path: string, languages: readonly { locale: string }[] = catalog.plugins.i18n.languages) {
  const language = languages.find(item => path.endsWith(`.${item.locale}.md`));
  return { topic: language ? path.slice(0, -language.locale.length - 4) + '.md' : path,
    locale: language?.locale ?? defaultDocumentationLocale };
}

export function resolveDocumentationPage(path: string, locale: string, paths: readonly string[],
  languages: readonly { locale: string }[] = catalog.plugins.i18n.languages, fallback = defaultDocumentationLocale) {
  const { topic } = documentIdentity(path, languages);
  const variants = new Map(paths.filter(item => documentIdentity(item, languages).topic === topic)
    .map(item => [documentIdentity(item, languages).locale, item]));
  return variants.get(locale) ?? variants.get(fallback) ?? path;
}

export function localizedPage(path: string, locale: string): string {
  return resolveDocumentationPage(path, locale, Object.keys(documentation));
}

export function documentationText(locale: string, key: keyof typeof catalog.extra.documentation,
  values: Record<string, string> = {}) {
  const messages = documentationLanguages.find(item => item.locale === locale)?.extra.documentation ?? catalog.extra.documentation;
  return messages[key].replace(/\{(\w+)\}/g, (match, name: string) => values[name] ?? match);
}

export function documentationGroups(locale: string) {
  return catalog.nav.map(group => {
    const [title, pages] = (Object.entries(group) as [string, string[]][])[0];
    const labels: Record<string, string> = documentationLanguages.find(item => item.locale === locale)?.nav_translations ?? {};
    return { title, label: labels[title] ?? title, pages: pages!.map(path => localizedPage(path, locale)) };
  });
}

export type DocumentationLink = { kind: 'page'; page: string; hash: string }
  | { kind: 'asset' | 'external'; url: string } | { kind: 'unavailable' };

/** Resolve relative links against their source page, never against the app URL. */
export function documentationLink(href: string, page: string, locale?: string): DocumentationLink {
  try {
    const local = new URL('https://offline.invalid/docs/');
    const input = href.startsWith(DOCS_URL) ? href.replace(DOCS_URL, local.href) : href;
    const url = new URL(input, new URL(page, local));
    if (url.origin !== local.origin) return /^(https?:|mailto:)$/.test(url.protocol)
      ? { kind: 'external', url: url.href } : { kind: 'unavailable' };
    let path = decodeURIComponent(url.pathname).replace(/^\/docs\//, '');
    const siteLanguage = documentationLanguages.find(item => path.startsWith(`${item.locale}/`));
    if (siteLanguage) path = path.slice(siteLanguage.locale.length + 1);
    const candidates = [path, `${path.replace(/\/$/, '')}.md`, `${path}index.md`];
    if (path === '') candidates.unshift('README.md');
    const target = candidates.find(candidate => documentation[candidate]);
    if (target) {
      const hash = decodeURIComponent(url.hash.slice(1));
      const source = siteLanguage ? localizedPage(target, siteLanguage.locale) : target;
      const translated = localizedPage(source, locale ?? documentIdentity(source).locale);
      // An existing deep link names a real source anchor. Do not guess its translation.
      const selected = hash ? source : translated;
      return { kind: 'page', page: selected, hash };
    }
    if (documentationAssets[path]) return { kind: 'asset', url: documentationAssets[path] };
    // Source-code links intentionally leave the manual and are marked as online.
    return { kind: 'external', url: `https://github.com/theAfish/open-agent-world/blob/dev${url.pathname}${url.hash}` };
  } catch { return { kind: 'unavailable' }; }
}

export function headingSlug(text: string) {
  // Match Python-Markdown's default ASCII slugifier, including accented text.
  return text.normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase()
    .replace(/[^\w\s-]/g, '').trim().replace(/[-\s]+/g, '-').replace(/^-|-$/g, '');
}

interface MarkdownNode {
  type: string;
  value?: string;
  children?: MarkdownNode[];
  data?: { hProperties?: Record<string, unknown> };
}

// Assign IDs while parsing, so duplicate headings remain stable under StrictMode
// and locale/search re-renders. The suffix matches MkDocs' duplicate-anchor form.
export function remarkDocumentationHeadings() {
  return (tree: MarkdownNode) => {
    const slugs = new Set<string>();
    const text = (node: MarkdownNode): string => node.value ?? node.children?.map(text).join('') ?? '';
    const visit = (node: MarkdownNode) => {
      if (node.type === 'heading') {
        const base = headingSlug(text(node));
        let id = base;
        let suffix = 0;
        while (!id || slugs.has(id)) id = `${base}_${++suffix}`;
        slugs.add(id);
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id } };
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}
