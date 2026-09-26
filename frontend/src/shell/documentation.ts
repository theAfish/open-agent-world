import type { Locale } from '../i18n';
import { DOCS_URL } from './helpChecks';

// Eager raw imports put the manual in the application bundle, including on first
// use without internet. Keep repository-only notes out of the reader and search.
const sources = import.meta.glob<string>([
  '../../../docs/**/*.md', '!../../../docs/assets/**',
  '!../../../docs/SHADOW_COLLECTION.md', '!../../../docs/SHADOW_GAS_BOUNDARY.md',
  '!../../../docs/READER_TRANSITION.md', '!../../../docs/matcreator-demo-plan.md',
  '!../../../docs/marketplace-production.md', '!../../../docs/pack-store-acceptance.md',
], { query: '?raw', import: 'default', eager: true });
const assets = import.meta.glob<string>('../../../docs/assets/**/*.{png,gif,jpg,jpeg,svg,webp}',
  { query: '?url', import: 'default', eager: true });

export const documentation = Object.fromEntries(Object.entries(sources).map(([path, source]) => {
  let markdown = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '');
  // Only the landing page uses MkDocs layout wrappers. Leave examples in other
  // pages untouched, including HTML/JSX inside fenced code blocks.
  if (path === '../../../docs/README.md') markdown = markdown
    .replace(/^<\/?div\b[^>]*>\s*$/gm, '')
    .replace(/(\]\([^\n)]+\))\{\s*\.[^}\n]+\}/g, '$1');
  return [path.replace('../../../docs/', ''), {
    title: markdown.match(/^#\s+(.+)$/m)?.[1].trim()
      ?? path.split('/').pop()!.replace(/\.md$/, '').replace(/-/g, ' ').replace(/^./, letter => letter.toUpperCase()), markdown,
  }];
}));

export const documentationAssets = Object.fromEntries(Object.entries(assets)
  .map(([path, url]) => [path.replace('../../../docs/', ''), url]));

const userPages = ['README.md', 'user-guide/index.md', 'install.md', 'user-guide/first-team.md',
  'user-guide/models.md', 'user-guide/canvas.md', 'user-guide/plugins.md', 'creator-packs.md',
  'user-guide/troubleshooting.md', 'user-guide/matcreator-example.zh-CN.md'];
const developerPages = ['developers/index.md', 'developers/setup.md', 'developers/first-plugin.md',
  'developers/agent-tools.md', 'developers/frontend.md', 'developers/testing.md',
  'pack-distribution.md', 'pack-store.md', 'developers/extension-points.md', 'developers/documentation.md'];
const canonicalPage = (path: string) => path.replace(/\.zh-CN\.md$/, '.md');

export function localizedPage(path: string, locale: Locale): string {
  const base = canonicalPage(path);
  const translated = base.replace(/\.md$/, '.zh-CN.md');
  return locale === 'zh-CN' && documentation[translated] ? translated : documentation[base] ? base : path;
}

export function documentationGroups(locale: Locale) {
  const seen = new Set([...userPages, ...developerPages].map(canonicalPage));
  const referencePages = Object.keys(documentation).filter(path => {
    const key = canonicalPage(path);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).sort((a, b) => documentation[a].title.localeCompare(documentation[b].title));
  return [
    { title: 'User guide', pages: userPages },
    { title: 'Build plugins', pages: developerPages },
    { title: 'Technical reference', pages: referencePages },
  ].map(group => ({ ...group, pages: group.pages.map(path => localizedPage(path, locale)) }));
}

export type DocumentationLink = { kind: 'page'; page: string; hash: string }
  | { kind: 'asset' | 'external'; url: string } | { kind: 'unavailable' };

/** Resolve relative links against their source page, never against the app URL. */
export function documentationLink(href: string, page: string): DocumentationLink {
  try {
    const local = new URL('https://offline.invalid/docs/');
    const input = href.startsWith(DOCS_URL) ? href.replace(DOCS_URL, local.href) : href;
    const url = new URL(input, new URL(page, local));
    if (url.origin !== local.origin) return /^(https?:|mailto:)$/.test(url.protocol)
      ? { kind: 'external', url: url.href } : { kind: 'unavailable' };
    const path = decodeURIComponent(url.pathname).replace(/^\/docs\//, '');
    const candidates = [path, `${path.replace(/\/$/, '')}.md`, `${path}index.md`];
    if (path === '') candidates.unshift('README.md');
    const target = candidates.find(candidate => documentation[candidate]);
    if (target) return { kind: 'page', page: target, hash: decodeURIComponent(url.hash.slice(1)) };
    if (documentationAssets[path]) return { kind: 'asset', url: documentationAssets[path] };
    // Source-code links intentionally leave the manual and are marked as online.
    return { kind: 'external', url: `https://github.com/theAfish/open-agent-world/blob/dev${url.pathname}${url.hash}` };
  } catch { return { kind: 'unavailable' }; }
}

export function headingSlug(text: string) {
  return text.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, '').trim().replace(/\s+/g, '-');
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
        while (slugs.has(id)) id = `${base}_${++suffix}`;
        slugs.add(id);
        node.data = { ...node.data, hProperties: { ...node.data?.hProperties, id } };
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}
